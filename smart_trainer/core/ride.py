"""The ride in progress: lifecycle, pauses, workout progress, recording and chart history."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from smart_trainer.config import Settings
from smart_trainer.core.control import Mode, TrainerController
from smart_trainer.core.recorder import Recorder, SessionInfo
from smart_trainer.core.sessions import SessionStore
from smart_trainer.core.state import Snapshot
from smart_trainer.core.workout import Workout, WorkoutRunner

AUTO_PAUSE_CADENCE = 20
AUTO_PAUSE_POWER = 25
INTENSITY_RANGE = (0.5, 1.5)
HISTORY_MAXLEN = 3 * 3600  # chart points kept: 3 h at 1 Hz
MAX_TICK_DT_S = 5.0  # a longer gap between ticks (stall, sleep, crash) counts as this much


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

    t: float
    power: float | None
    hr: int | None
    cadence: float | None
    target: float | None


def _no_blocker() -> str | None:
    """Default ride blocker: nothing prevents a ride from starting.

    Returns:
        str | None: Always ``None``.
    """
    return None


@dataclass(eq=False)
class Ride:
    """The current ride (free ride or workout) from start to finish.

    Built idle; :meth:`start` or :meth:`resume` begins a ride.

    Attributes:
        settings (Settings): User settings; FTP is read when a ride starts.
        controller (TrainerController): Trainer control, switched ERG/resistance.
        store (SessionStore): Creates and finalizes session files.
        messages (deque[str]): User-facing status messages.
        tick_s (float): Seconds per :meth:`tick`.
        blocker (Callable[[], str | None]): Returns why a ride can't start now
            (e.g. a calibration is running), or ``None``.
        state (RideState): Current ride state.
        runner (WorkoutRunner | None): Workout runner, if riding a workout.
        recorder (Recorder | None): Session recorder while riding.
        manual_pause (bool): Whether the rider paused manually.
        auto_paused (bool): Whether the ride is auto-paused (not pedalling).
        erg_enabled (bool): Whether workout targets drive ERG mode.
        seconds (float): Recorded seconds, excluding pauses.
        history (deque[HistoryPoint]): Points for the live chart.
    """

    settings: Settings = field(repr=False)
    controller: TrainerController = field(repr=False)
    store: SessionStore = field(repr=False)
    messages: deque[str] = field(repr=False)
    tick_s: float
    blocker: Callable[[], str | None] = field(default=_no_blocker, repr=False)
    state: RideState = field(default=RideState.IDLE, init=False)
    runner: WorkoutRunner | None = field(default=None, init=False)
    recorder: Recorder | None = field(default=None, init=False)
    manual_pause: bool = field(default=False, init=False)
    auto_paused: bool = field(default=False, init=False)
    erg_enabled: bool = field(default=True, init=False)
    seconds: float = field(default=0.0, init=False)
    history: deque[HistoryPoint] = field(
        default_factory=lambda: deque(maxlen=HISTORY_MAXLEN), init=False, repr=False
    )
    _last_step: int | None = field(default=None, init=False, repr=False)
    _completed_announced: bool = field(default=False, init=False, repr=False)

    @property
    def active(self) -> bool:
        """Whether a ride is in progress (riding or finishing).

        Returns:
            bool: ``True`` unless idle.
        """
        return self.state != RideState.IDLE

    # ---------------------------------------------------------------- lifecycle
    def start(self, workout: Workout | None) -> None:
        """Start recording a new ride.

        Args:
            workout (Workout | None): Workout to ride, or ``None`` for a free ride.

        Raises:
            RuntimeError: If a ride can't start now (e.g. a calibration is running).
        """
        self._check_can_start()
        name = workout.name if workout else f"Free ride {datetime.now():%d %b}"
        ftp = self.settings.ftp  # read once: the recorder and the runner must agree
        self.recorder = self.store.create(name, workout, ftp)
        self.runner = WorkoutRunner(workout, ftp) if workout and workout.steps else None
        self.seconds = 0.0
        self.history.clear()
        self._begin("Ride started")

    def resume(self, info: SessionInfo) -> None:
        """Continue a crashed session: same file, workout resumes at the saved elapsed time.

        Args:
            info (SessionInfo): The unfinished session to resume.

        Raises:
            RuntimeError: If a ride can't start now (e.g. a calibration is running).
        """
        self._check_can_start()
        self.recorder = self.store.open(info)
        self.runner = (
            WorkoutRunner(info.workout, info.ftp, info.elapsed_s)
            if info.workout and info.workout.steps
            else None
        )
        self.history.clear()
        active, prev_t = 0.0, None
        for r in self.recorder.samples():  # restore the chart from what was recorded
            if not r["paused"]:  # same clamp as the live ticks; crash gaps don't count
                gap = self.tick_s if prev_t is None else r["t"] - prev_t
                active += min(max(gap, 0.0), MAX_TICK_DT_S)
            prev_t = r["t"]
            self.history.append(
                HistoryPoint(active, r["power"], r["hr"], r["cadence"], r["target"])
            )
        self.seconds = active
        self._begin("Session resumed")

    def _check_can_start(self) -> None:
        """Refuse to start when the blocker gives a reason.

        Raises:
            RuntimeError: With the blocker's reason.
        """
        if reason := self.blocker():
            raise RuntimeError(reason)

    def _begin(self, message: str) -> None:
        """Enter the riding state.

        Args:
            message (str): Status message to post.
        """
        self.state = RideState.RIDING
        self.manual_pause = False
        self._completed_announced = False
        self._last_step = self.runner.step_index() if self.runner else None
        if self.runner is None:
            self.controller.set_mode(Mode.RESISTANCE)
        self.messages.append(message)

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
        try:
            return await self.store.finalize(rec, upload)
        finally:  # even if finalizing fails, don't leave the ride stuck in FINISHING
            rec.close()
            self.recorder, self.runner = None, None
            self.state = RideState.IDLE
            self.controller.set_mode(Mode.RESISTANCE)

    def close(self) -> None:
        """Close the recorder without finishing: the session stays recoverable."""
        if self.recorder:
            self.recorder.close()

    def recoverable_sessions(self) -> list[SessionInfo]:
        """List crashed sessions, excluding the one currently recording.

        Returns:
            list[SessionInfo]: Sessions still marked ``active``.
        """
        return self.store.unfinished(exclude=self.recorder.path if self.recorder else None)

    # --------------------------------------------------------------------- tick
    def tick(self, snap: Snapshot, now_wall: float, dt: float | None = None) -> None:
        """Advance the ride by one tick: pauses, workout, control mode, recording, chart.

        Does nothing unless riding.

        Args:
            snap (Snapshot): Latest effective metrics.
            now_wall (float): Current unix time.
            dt (float | None): Seconds since the previous tick; defaults to ``tick_s``.
        """
        if self.state != RideState.RIDING:
            return
        dt = self.tick_s if dt is None else dt
        pedaling = (snap.cadence or 0) >= AUTO_PAUSE_CADENCE or (
            snap.power or 0
        ) >= AUTO_PAUSE_POWER
        self.auto_paused = not pedaling
        paused = self.manual_pause or self.auto_paused
        target = self._advance_workout(paused, dt)
        self._update_control_mode(target)
        if not paused:
            self.seconds += dt
        if self.recorder:
            self.recorder.record(
                snap,
                self.runner.elapsed if self.runner else self.seconds,
                target,
                paused,
                self.controller.mode.value,
                now_wall,
            )
        self.history.append(HistoryPoint(self.seconds, snap.power, snap.hr, snap.cadence, target))

    def _advance_workout(self, paused: bool, dt: float) -> float | None:
        """Advance the workout, starting a lap on each new step.

        Args:
            paused (bool): Whether the ride is paused (the workout clock stops).
            dt (float): Seconds to advance the workout by.

        Returns:
            float | None: The current target in watts, or ``None``.
        """
        if self.runner is None:
            return None
        self.runner.paused = paused
        self.runner.advance(dt)
        target = self.runner.target_w()
        idx = self.runner.step_index()
        if self._last_step is not None and idx != self._last_step and self.recorder:
            self.recorder.new_lap()
        self._last_step = idx
        if self.runner.finished and target is None and not self._completed_announced:
            self._completed_announced = True
            self.messages.append("Workout complete! Keep riding or press Finish.")
        return target

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

    # ----------------------------------------------------------------- controls
    def current_target(self) -> float | None:
        """Current workout target power.

        Returns:
            float | None: Target in watts, or ``None`` if no workout target.
        """
        return self.runner.target_w() if self.runner else None

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
            lo, hi = INTENSITY_RANGE
            self.runner.intensity = max(lo, min(hi, round(self.runner.intensity + delta, 2)))

    def set_erg(self, enabled: bool) -> None:
        """Enable or disable ERG mode for workout targets.

        Args:
            enabled (bool): Whether workout targets should drive ERG mode.
        """
        self.erg_enabled = enabled
        if not enabled and self.controller.mode == Mode.ERG:
            self.controller.set_mode(Mode.RESISTANCE)
