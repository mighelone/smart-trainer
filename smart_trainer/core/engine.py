"""The app core: owns devices, source selection, trainer control, workout and recording.

Runs a 1 Hz loop on the asyncio event loop. The UI only reads engine state and calls
its methods, so the engine is UI-agnostic.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from smart_trainer.ble.device import ControllableTrainer, Device
from smart_trainer.config import PairedDevice, Settings, get_api_key, sessions_dir
from smart_trainer.core.control import Mode, TrainerController
from smart_trainer.core.recorder import Recorder, SessionInfo, find_unfinished
from smart_trainer.core.sources import SourceSelector
from smart_trainer.core.state import ConnState, Role, Snapshot
from smart_trainer.core.workout import Workout, WorkoutRunner
from smart_trainer.export.fit import export_fit

log = logging.getLogger(__name__)

AUTO_PAUSE_CADENCE = 20
AUTO_PAUSE_POWER = 25


class RideState(StrEnum):
    IDLE = "idle"
    RIDING = "riding"
    FINISHING = "finishing"


@dataclass
class HistoryPoint:
    t: float  # seconds since ride start (wall clock)
    power: float | None
    hr: int | None
    cadence: float | None
    target: float | None


class Engine:
    def __init__(self, settings: Settings, sessions: Path | None = None,
                 tick_s: float = 1.0) -> None:
        self.settings = settings
        self.sessions = sessions or sessions_dir()
        self.tick_s = tick_s
        self.devices: dict[str, Device] = {}
        self.selector = SourceSelector()
        self.controller = TrainerController(settings.power_match)
        self.snapshot = Snapshot()
        self.state = RideState.IDLE
        self.runner: WorkoutRunner | None = None
        self.recorder: Recorder | None = None
        self.manual_pause = False
        self.auto_paused = False
        self.erg_enabled = True
        self.ride_started_at: float | None = None
        self.ride_seconds = 0.0  # wall-clock seconds recorded (excl. pauses)
        self.history: deque[HistoryPoint] = deque(maxlen=3 * 3600)
        self.messages: deque[str] = deque(maxlen=20)
        self.last_fit: Path | None = None
        self._last_step: int | None = None
        self._task: asyncio.Task | None = None
        self._apply_assignment()

    # ------------------------------------------------------------------ devices
    def add_device(self, dev: Device, roles: list[Role] | None = None) -> None:
        old = self.devices.get(dev.id)
        if old is not None:
            asyncio.ensure_future(old.stop())
        self.devices[dev.id] = dev
        dev.subscribe(self.selector.on_reading)
        for role in roles or []:
            self.settings.role_assignment[role.value] = dev.id
        self._apply_assignment()
        dev.start()

    async def remove_device(self, device_id: str) -> None:
        dev = self.devices.pop(device_id, None)
        if dev:
            await dev.stop()
        self.settings.devices.pop(device_id, None)
        for role, did in list(self.settings.role_assignment.items()):
            if did == device_id:
                del self.settings.role_assignment[role]
        self._apply_assignment()

    def pair(self, dev: Device, roles: list[Role]) -> None:
        self.settings.devices[dev.id] = PairedDevice(address=dev.address, name=dev.name,
                                                     driver=dev.DRIVER)
        self.add_device(dev, roles)
        self.settings.save()

    def _apply_assignment(self) -> None:
        ra = {Role(k): v for k, v in self.settings.role_assignment.items()}
        trainer_id = ra.get(Role.TRAINER)
        if self.settings.power_source == "trainer" and trainer_id:
            ra[Role.POWER] = trainer_id
        self.selector.assignment = ra
        self.selector.trainer_id = trainer_id
        dev = self.devices.get(trainer_id) if trainer_id else None
        self.controller.trainer = dev if isinstance(dev, ControllableTrainer) else None
        self.controller.use_pedals = (self.settings.power_source == "pedals"
                                      and ra.get(Role.POWER) not in (None, trainer_id))
        self.controller.force_resend()

    def set_power_source(self, source: str) -> None:
        self.settings.power_source = source
        self.settings.save()
        self._apply_assignment()

    def device_for(self, role: Role) -> Device | None:
        did = self.selector.assignment.get(role)
        return self.devices.get(did) if did else None

    def trainer_connected(self) -> bool:
        d = self.device_for(Role.TRAINER)
        return bool(d and d.state == ConnState.CONNECTED)

    # --------------------------------------------------------------------- loop
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="engine")

    async def shutdown(self) -> None:
        if self._task:
            self._task.cancel()
        for d in list(self.devices.values()):
            await d.stop()
        if self.recorder:
            self.recorder.close()  # session stays 'active' -> recoverable

    async def _loop(self) -> None:
        next_t = time.monotonic()
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("engine tick failed")
            next_t += self.tick_s
            await asyncio.sleep(max(0.0, next_t - time.monotonic()))

    async def tick(self, now_wall: float | None = None) -> None:
        now_wall = now_wall or time.time()
        snap = self.selector.snapshot()
        self.snapshot = snap
        target = None
        if self.state == RideState.RIDING:
            pedaling = (snap.cadence or 0) >= AUTO_PAUSE_CADENCE or (snap.power or 0) >= AUTO_PAUSE_POWER
            self.auto_paused = not pedaling
            paused = self.manual_pause or self.auto_paused
            if self.runner:
                self.runner.paused = paused
                self.runner.advance(self.tick_s)
                target = self.runner.target_w()
                idx = self.runner.step_index()
                if self._last_step is not None and idx != self._last_step and self.recorder:
                    self.recorder.new_lap()
                self._last_step = idx
                if self.runner.finished and target is None and "complete" not in "".join(self.messages):
                    self.messages.append("Workout complete! Keep riding or press Finish.")
            self._update_control_mode(target)
            if not paused:
                self.ride_seconds += self.tick_s
            if self.recorder:
                self.recorder.record(snap, self.runner.elapsed if self.runner else self.ride_seconds,
                                     target, paused, self.controller.mode.value, now_wall)
            self.history.append(HistoryPoint(self.ride_seconds, snap.power, snap.hr, snap.cadence, target))
        pedal_avg = self.selector.avg_power(self.selector.assignment.get(Role.POWER))
        trainer_avg = self.selector.avg_power(self.selector.trainer_id)
        await self.controller.apply(snap, pedal_avg, trainer_avg, self.trainer_connected(), now_wall)

    def _update_control_mode(self, target: float | None) -> None:
        if self.runner is None:
            return
        if target is not None and self.erg_enabled:
            self.controller.erg_target = target
            self.controller.set_mode(Mode.ERG)
        elif self.controller.mode == Mode.ERG:
            self.controller.set_mode(Mode.RESISTANCE)

    # ------------------------------------------------------------------- riding
    def current_target(self) -> float | None:
        return self.runner.target_w() if self.runner else None

    def start_ride(self, workout: Workout | None) -> None:
        name = workout.name if workout else f"Free ride {datetime.now():%d %b}"
        self.recorder = Recorder.create(self.sessions, name, self.settings.ftp, workout)
        self.runner = WorkoutRunner(workout, self.settings.ftp) if workout and workout.steps else None
        self._begin()

    def resume_session(self, info: SessionInfo) -> None:
        """Continue a crashed session: same file, workout resumes at the saved elapsed time."""
        self.recorder = Recorder.open(info.path)
        self.runner = (WorkoutRunner(info.workout, info.ftp, info.elapsed_s)
                       if info.workout and info.workout.steps else None)
        self.history.clear()
        active = 0.0
        for r in self.recorder.samples():  # restore the chart from what was recorded
            if not r["paused"]:
                active += self.tick_s
            self.history.append(HistoryPoint(active, r["power"], r["hr"], r["cadence"], r["target"]))
        self.ride_seconds = active
        self._begin(resumed=True)

    def _begin(self, resumed: bool = False) -> None:
        self.state = RideState.RIDING
        self.manual_pause = False
        self.ride_started_at = time.time()
        if not resumed:
            self.ride_seconds = 0.0
            self.history.clear()
        self._last_step = self.runner.step_index() if self.runner else None
        if self.runner is None:
            self.controller.set_mode(Mode.RESISTANCE)
        self.messages.append("Session resumed" if resumed else "Ride started")

    def toggle_pause(self) -> None:
        self.manual_pause = not self.manual_pause

    def skip_step(self) -> None:
        if self.runner:
            self.runner.skip()

    def lap(self) -> None:
        if self.recorder:
            self.recorder.new_lap()

    def change_intensity(self, delta: float) -> None:
        if self.runner:
            self.runner.intensity = max(0.5, min(1.5, round(self.runner.intensity + delta, 2)))

    def set_erg(self, enabled: bool) -> None:
        self.erg_enabled = enabled
        if not enabled and self.controller.mode == Mode.ERG:
            self.controller.set_mode(Mode.RESISTANCE)

    async def finish(self, upload: bool | None = None) -> Path | None:
        """Stop recording, export FIT, and optionally upload. Returns the FIT path."""
        if not self.recorder:
            return None
        self.state = RideState.FINISHING
        rec = self.recorder
        fit = await self.finalize(rec, upload)
        rec.close()
        self.recorder, self.runner = None, None
        self.state = RideState.IDLE
        self.controller.set_mode(Mode.RESISTANCE)
        self.last_fit = fit
        return fit

    async def finalize(self, rec: Recorder, upload: bool | None = None) -> Path | None:
        info = rec.info()
        fit = None
        if info.n_samples:
            fit = export_fit(rec, rec.path.with_suffix(".fit"))
        rec.set_status("finished")
        if fit and (self.settings.auto_upload if upload is None else upload):
            await self.upload(rec, fit, info)
        return fit

    async def upload(self, rec: Recorder, fit: Path, info: SessionInfo | None = None) -> bool:
        info = info or rec.info()
        if self.settings._readonly:
            self.messages.append("Demo mode: uploads are disabled")
            return False
        key = get_api_key()
        if not key:
            self.messages.append("No intervals.icu API key: saved locally only")
            return False
        from smart_trainer.sync.intervals import IntervalsClient
        client = IntervalsClient(key, self.settings.intervals_athlete_id)
        try:
            aid = await client.upload_activity(
                fit, info.name, paired_event_id=info.workout.source_id if info.workout else None)
            rec.set_status("uploaded", upload_id=aid, upload_error=None)
            self.messages.append(f"Uploaded to intervals.icu ({aid})")
            return True
        except Exception as exc:
            rec.set_status("finished", upload_error=str(exc))
            self.messages.append(f"Upload failed: {exc}")
            return False
        finally:
            await client.aclose()

    async def finish_crashed(self, info: SessionInfo, upload: bool | None = None) -> Path | None:
        rec = Recorder.open(info.path)
        try:
            return await self.finalize(rec, upload)
        finally:
            rec.close()

    def unfinished_sessions(self) -> list[SessionInfo]:
        current = self.recorder.path if self.recorder else None
        return [s for s in find_unfinished(self.sessions) if s.path != current]

    def discard(self, info: SessionInfo) -> None:
        """Move a session out of the way (kept in sessions/discarded, never deleted)."""
        dest = self.sessions / "discarded"
        dest.mkdir(exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(info.path) + suffix)
            if p.exists():
                p.replace(dest / p.name)
