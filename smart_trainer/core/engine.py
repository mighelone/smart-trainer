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

from smart_trainer.ble.device import (
    Calibratable,
    CalibrationError,
    CalibrationPhase,
    CalibrationProgress,
    CalibrationResult,
    ControllableTrainer,
    Device,
)
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
    """Lifecycle state of a ride."""

    IDLE = "idle"
    RIDING = "riding"
    FINISHING = "finishing"


@dataclass
class HistoryPoint:
    """One point of the live ride chart.

    Attributes:
        t (float): Active seconds since ride start.
        power (float | None): Power in watts.
        hr (int | None): Heart rate in bpm.
        cadence (float | None): Cadence in rpm.
        target (float | None): Workout target power in watts.
    """

    t: float  # seconds since ride start (wall clock)
    power: float | None
    hr: int | None
    cadence: float | None
    target: float | None


class Engine:
    """Application core tying devices, control, workout and recording together.

    Attributes:
        settings (Settings): User settings.
        sessions (Path): Directory where sessions are recorded.
        tick_s (float): Loop interval in seconds.
        devices (dict[str, Device]): Active devices by id.
        selector (SourceSelector): Resolves readings into snapshots.
        controller (TrainerController): Trainer control logic.
        snapshot (Snapshot): Latest effective metrics.
        state (RideState): Current ride state.
        runner (WorkoutRunner | None): Workout runner, if riding a workout.
        recorder (Recorder | None): Session recorder while riding.
        manual_pause (bool): Whether the rider paused manually.
        auto_paused (bool): Whether the ride is auto-paused (not pedalling).
        erg_enabled (bool): Whether workout targets drive ERG mode.
        ride_started_at (float | None): Unix time the ride started.
        ride_seconds (float): Recorded seconds, excluding pauses.
        history (deque[HistoryPoint]): Points for the live chart.
        messages (deque[str]): User-facing status messages.
        last_fit (Path | None): Path of the last exported FIT file.
        calibrating_id (str | None): Id of the device being calibrated, if any.
        calibration (CalibrationProgress | None): Latest progress of the last calibration.
    """

    def __init__(
        self, settings: Settings, sessions: Path | None = None, tick_s: float = 1.0
    ) -> None:
        """Initialize the engine.

        Args:
            settings (Settings): User settings.
            sessions (Path | None): Session directory; defaults to :func:`sessions_dir`.
            tick_s (float): Loop interval in seconds.
        """
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
        self.calibrating_id: str | None = None
        self.calibration: CalibrationProgress | None = None
        self._last_step: int | None = None
        self._task: asyncio.Task | None = None
        self._apply_assignment()

    # ------------------------------------------------------------------ devices
    def add_device(self, dev: Device, roles: list[Role] | None = None) -> None:
        """Add (or replace) a device, assign it roles and start it.

        Args:
            dev (Device): The device to add.
            roles (list[Role] | None): Roles to assign to the device.
        """
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
        """Stop and forget a device, removing its pairing and role assignments.

        Args:
            device_id (str): Id of the device to remove.
        """
        dev = self.devices.pop(device_id, None)
        if dev:
            await dev.stop()
        self.settings.devices.pop(device_id, None)
        for role, did in list(self.settings.role_assignment.items()):
            if did == device_id:
                del self.settings.role_assignment[role]
        self._apply_assignment()

    def pair(self, dev: Device, roles: list[Role]) -> None:
        """Pair a device: persist it in the settings and add it to the engine.

        Args:
            dev (Device): The device to pair.
            roles (list[Role]): Roles to assign to the device.
        """
        self.settings.devices[dev.id] = PairedDevice(
            address=dev.address, name=dev.name, driver=dev.DRIVER
        )
        self.add_device(dev, roles)
        self.settings.save()

    def _apply_assignment(self) -> None:
        """Push the role assignment from the settings to the selector and controller."""
        ra = {Role(k): v for k, v in self.settings.role_assignment.items()}
        trainer_id = ra.get(Role.TRAINER)
        if self.settings.power_source == "trainer" and trainer_id:
            ra[Role.POWER] = trainer_id
        self.selector.assignment = ra
        self.selector.trainer_id = trainer_id
        dev = self.devices.get(trainer_id) if trainer_id else None
        self.controller.trainer = dev if isinstance(dev, ControllableTrainer) else None
        self.controller.use_pedals = self.settings.power_source == "pedals" and ra.get(
            Role.POWER
        ) not in (None, trainer_id)
        self.controller.force_resend()

    def set_power_source(self, source: str) -> None:
        """Choose the preferred power source and persist it.

        Args:
            source (str): ``"pedals"`` or ``"trainer"``.
        """
        self.settings.power_source = source
        self.settings.save()
        self._apply_assignment()

    def device_for(self, role: Role) -> Device | None:
        """Get the device assigned to a role.

        Args:
            role (Role): The role to look up.

        Returns:
            Device | None: The assigned device, or ``None``.
        """
        did = self.selector.assignment.get(role)
        return self.devices.get(did) if did else None

    def trainer_connected(self) -> bool:
        """Whether the assigned trainer is connected.

        Returns:
            bool: ``True`` if a trainer is assigned and connected.
        """
        device = self.device_for(Role.TRAINER)
        return bool(device and device.state == ConnState.CONNECTED)

    # --------------------------------------------------------------------- loop
    def start(self) -> None:
        """Start the engine loop if not already running."""
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="engine")

    async def shutdown(self) -> None:
        """Stop the loop and all devices, leaving any active session recoverable."""
        if self._task:
            self._task.cancel()
        for d in list(self.devices.values()):
            await d.stop()
        if self.recorder:
            self.recorder.close()  # session stays 'active' -> recoverable

    async def _loop(self) -> None:
        """Run :meth:`tick` every ``tick_s`` seconds until cancelled."""
        next_t = time.monotonic()
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("engine tick failed")
            next_t += self.tick_s
            await asyncio.sleep(max(0.0, next_t - time.monotonic()))

    async def tick(self, now_wall: float | None = None) -> None:
        """Run one engine step: snapshot, workout progress, recording and control.

        Args:
            now_wall (float | None): Current unix time; defaults to ``time.time()``.
        """
        now_wall = now_wall or time.time()
        snap = self.selector.snapshot()
        self.snapshot = snap
        target = None
        if self.state == RideState.RIDING:
            pedaling = (snap.cadence or 0) >= AUTO_PAUSE_CADENCE or (
                snap.power or 0
            ) >= AUTO_PAUSE_POWER
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
                if (
                    self.runner.finished
                    and target is None
                    and "complete" not in "".join(self.messages)
                ):
                    self.messages.append("Workout complete! Keep riding or press Finish.")
            self._update_control_mode(target)
            if not paused:
                self.ride_seconds += self.tick_s
            if self.recorder:
                self.recorder.record(
                    snap,
                    self.runner.elapsed if self.runner else self.ride_seconds,
                    target,
                    paused,
                    self.controller.mode.value,
                    now_wall,
                )
            self.history.append(
                HistoryPoint(self.ride_seconds, snap.power, snap.hr, snap.cadence, target)
            )
        if self.calibrating_id is not None and self.calibrating_id == self.selector.trainer_id:
            return  # never send resistance commands while the trainer spins down
        pedal_avg = self.selector.avg_power(self.selector.assignment.get(Role.POWER))
        trainer_avg = self.selector.avg_power(self.selector.trainer_id)
        await self.controller.apply(
            snap, pedal_avg, trainer_avg, self.trainer_connected(), now_wall
        )

    def _update_control_mode(self, target: float | None) -> None:
        """Switch between ERG and resistance mode according to the workout target.

        Args:
            target (float | None): Current workout target in watts, if any.
        """
        if self.runner is None:
            return
        if target is not None and self.erg_enabled:
            self.controller.erg_target = target
            self.controller.set_mode(Mode.ERG)
        elif self.controller.mode == Mode.ERG:
            self.controller.set_mode(Mode.RESISTANCE)

    # -------------------------------------------------------------- calibration
    def calibration_blocker(self, device_id: str) -> str | None:
        """Why a device can't be calibrated right now.

        Args:
            device_id (str): Id of the device to calibrate.

        Returns:
            str | None: A user-facing reason, or ``None`` if calibration can start.
        """
        dev = self.devices.get(device_id)
        if not isinstance(dev, Calibratable):
            return "This device does not support calibration."
        if self.state != RideState.IDLE:
            return "Finish the ride before calibrating."
        if self.calibrating_id is not None:
            return "A calibration is already running."
        if dev.state != ConnState.CONNECTED:
            return "The device is not connected."
        return None

    async def calibrate(self, device_id: str) -> CalibrationResult:
        """Calibrate a device; progress is published in :attr:`calibration`.

        Trainer control is suspended while the trainer itself is calibrating, and
        rides can't start until it finishes.

        Args:
            device_id (str): Id of the device to calibrate.

        Returns:
            CalibrationResult: The outcome.

        Raises:
            CalibrationError: If calibration is not possible now or fails.
        """
        reason = self.calibration_blocker(device_id)
        if reason:
            raise CalibrationError(reason)
        dev = self.devices[device_id]
        assert isinstance(dev, Calibratable)

        def on_progress(p: CalibrationProgress) -> None:
            """Publish a progress update.

            Args:
                p (CalibrationProgress): The update.
            """
            self.calibration = p

        self.calibrating_id = device_id
        self.calibration = CalibrationProgress(CalibrationPhase.STARTING, "Starting…")
        try:
            result = await dev.calibrate(on_progress)
        except CalibrationError as exc:
            self.calibration = CalibrationProgress(CalibrationPhase.FAILED, str(exc))
            self.messages.append(f"{dev.name}: calibration failed")
            raise
        except asyncio.CancelledError:
            self.calibration = CalibrationProgress(
                CalibrationPhase.FAILED, "Calibration cancelled."
            )
            raise
        except Exception as exc:
            log.exception("%s: calibration failed", dev.name)
            msg = f"Calibration failed: {exc or type(exc).__name__}"
            self.calibration = CalibrationProgress(CalibrationPhase.FAILED, msg)
            raise CalibrationError(msg) from exc
        finally:
            self.calibrating_id = None
            self.controller.force_resend()
        self.calibration = CalibrationProgress(CalibrationPhase.DONE, result.message, fraction=1.0)
        self.messages.append(f"{dev.name}: {result.message}")
        return result

    # ------------------------------------------------------------------- riding
    def current_target(self) -> float | None:
        """Current workout target power.

        Returns:
            float | None: Target in watts, or ``None`` if no workout target.
        """
        return self.runner.target_w() if self.runner else None

    def start_ride(self, workout: Workout | None) -> None:
        """Start recording a new ride.

        Args:
            workout (Workout | None): Workout to ride, or ``None`` for a free ride.

        Raises:
            RuntimeError: If a calibration is running.
        """
        self._check_not_calibrating()
        name = workout.name if workout else f"Free ride {datetime.now():%d %b}"
        self.recorder = Recorder.create(self.sessions, name, self.settings.ftp, workout)
        self.runner = (
            WorkoutRunner(workout, self.settings.ftp) if workout and workout.steps else None
        )
        self._begin()

    def resume_session(self, info: SessionInfo) -> None:
        """Continue a crashed session: same file, workout resumes at the saved elapsed time.

        Args:
            info (SessionInfo): The unfinished session to resume.

        Raises:
            RuntimeError: If a calibration is running.
        """
        self._check_not_calibrating()
        self.recorder = Recorder.open(info.path)
        self.runner = (
            WorkoutRunner(info.workout, info.ftp, info.elapsed_s)
            if info.workout and info.workout.steps
            else None
        )
        self.history.clear()
        active = 0.0
        for r in self.recorder.samples():  # restore the chart from what was recorded
            if not r["paused"]:
                active += self.tick_s
            self.history.append(
                HistoryPoint(active, r["power"], r["hr"], r["cadence"], r["target"])
            )
        self.ride_seconds = active
        self._begin(resumed=True)

    def _check_not_calibrating(self) -> None:
        """Refuse to ride while a device is being calibrated.

        Raises:
            RuntimeError: If a calibration is running.
        """
        if self.calibrating_id is not None:
            raise RuntimeError("Wait for the calibration to finish before riding.")

    def _begin(self, resumed: bool = False) -> None:
        """Enter the riding state.

        Args:
            resumed (bool): Whether an existing session is being resumed.
        """
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
        """Toggle the manual pause."""
        self.manual_pause = not self.manual_pause

    def skip_step(self) -> None:
        """Skip to the next workout step."""
        if self.runner:
            self.runner.skip()

    def lap(self) -> None:
        """Start a new lap in the recording."""
        if self.recorder:
            self.recorder.new_lap()

    def change_intensity(self, delta: float) -> None:
        """Adjust the workout intensity multiplier (clamped to 0.5-1.5).

        Args:
            delta (float): Amount to add to the intensity (e.g. ``0.05``).
        """
        if self.runner:
            self.runner.intensity = max(0.5, min(1.5, round(self.runner.intensity + delta, 2)))

    def set_erg(self, enabled: bool) -> None:
        """Enable or disable ERG mode for workout targets.

        Args:
            enabled (bool): Whether workout targets should drive ERG mode.
        """
        self.erg_enabled = enabled
        if not enabled and self.controller.mode == Mode.ERG:
            self.controller.set_mode(Mode.RESISTANCE)

    async def finish(self, upload: bool | None = None) -> Path | None:
        """Stop recording, export FIT, and optionally upload.

        Args:
            upload (bool | None): Whether to upload; defaults to the
                ``auto_upload`` setting.

        Returns:
            Path | None: The FIT file path, or ``None`` if nothing was recorded.
        """
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
        """Export a session to FIT, mark it finished and optionally upload it.

        Args:
            rec (Recorder): The session recorder.
            upload (bool | None): Whether to upload; defaults to the
                ``auto_upload`` setting.

        Returns:
            Path | None: The FIT file path, or ``None`` if the session has no samples.
        """
        info = rec.info()
        fit = None
        if info.n_samples:
            fit = export_fit(rec, rec.path.with_suffix(".fit"))
        rec.set_status("finished")
        if fit and (self.settings.auto_upload if upload is None else upload):
            await self.upload(rec, fit, info)
        return fit

    async def upload(self, rec: Recorder, fit: Path, info: SessionInfo | None = None) -> bool:
        """Upload a FIT file to intervals.icu and record the outcome in the session.

        Args:
            rec (Recorder): The session recorder.
            fit (Path): Path of the FIT file to upload.
            info (SessionInfo | None): Session info; read from ``rec`` if omitted.

        Returns:
            bool: ``True`` if the upload succeeded.
        """
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
                fit, info.name, paired_event_id=info.workout.source_id if info.workout else None
            )
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
        """Finalize a crashed session without resuming it.

        Args:
            info (SessionInfo): The unfinished session.
            upload (bool | None): Whether to upload; defaults to the
                ``auto_upload`` setting.

        Returns:
            Path | None: The FIT file path, or ``None`` if the session has no samples.
        """
        rec = Recorder.open(info.path)
        try:
            return await self.finalize(rec, upload)
        finally:
            rec.close()

    def unfinished_sessions(self) -> list[SessionInfo]:
        """List crashed sessions, excluding the one currently recording.

        Returns:
            list[SessionInfo]: Sessions still marked ``active``.
        """
        current = self.recorder.path if self.recorder else None
        return [s for s in find_unfinished(self.sessions) if s.path != current]

    def discard(self, info: SessionInfo) -> None:
        """Move a session out of the way (kept in sessions/discarded, never deleted).

        Args:
            info (SessionInfo): The session to discard.
        """
        dest = self.sessions / "discarded"
        dest.mkdir(exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(info.path) + suffix)
            if p.exists():
                p.replace(dest / p.name)
