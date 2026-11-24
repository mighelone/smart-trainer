"""Trainer control modes and pedal power matching."""

from __future__ import annotations

import logging
import time
from enum import StrEnum

from smart_trainer.ble.device import ControllableTrainer, Device
from smart_trainer.config import PowerMatch
from smart_trainer.core.shifting import VirtualGearbox
from smart_trainer.core.state import Assignment, Snapshot

log = logging.getLogger(__name__)


class Mode(StrEnum):
    """Trainer control mode."""

    ERG = "erg"
    RESISTANCE = "resistance"
    SLOPE = "slope"


class PowerMatcher:
    """Makes the *pedal* power hit the ERG target while the trainer closes its own loop.

    1. Learns the ratio trainer_power / pedal_power (EMA, only while riding steadily) and
       commands ``target * ratio``. This handles a proportional calibration difference
       instantly on target changes.
    2. A slow integral term removes any residual pedal error (e.g. trainers whose ERG loop
       doesn't track their own reported power exactly).

    The total correction is clamped to ±max_offset_w.

    Attributes:
        cfg (PowerMatch): Tuning parameters.
        ratio (float): Learned trainer/pedal power ratio.
        integral (float): Accumulated integral correction in watts.
    """

    def __init__(self, cfg: PowerMatch) -> None:
        """Initialize the matcher.

        Args:
            cfg (PowerMatch): Tuning parameters.
        """
        self.cfg = cfg
        self.ratio = 1.0
        self.integral = 0.0
        self._last_target: float | None = None
        self._target_changed_at = 0.0

    def reset(self) -> None:
        """Reset the integral term and the target-change tracking."""
        self.integral = 0.0
        self._last_target = None

    def command(self, target: float, snapshot: Snapshot, now: float | None = None) -> float:
        """Compute the ERG command to send to the trainer.

        Args:
            target (float): Desired pedal power in watts.
            snapshot (Snapshot): Current metrics, including the pedal and trainer
                power averages.
            now (float | None): Current monotonic time; defaults to ``time.monotonic()``.

        Returns:
            float: Power command in watts, within ±max_offset_w of ``target``.
        """
        now = now if now is not None else time.monotonic()
        cfg = self.cfg
        if self._last_target is None or abs(target - self._last_target) >= 1:
            self._last_target = target
            self._target_changed_at = now
        settled = now - self._target_changed_at >= cfg.settle_s
        riding = (snapshot.cadence or 0) >= cfg.min_cadence
        pedal_avg, trainer_avg = snapshot.pedal_avg, snapshot.trainer_avg
        if settled and riding and pedal_avg and pedal_avg > cfg.min_power_w:
            if trainer_avg and trainer_avg > cfg.min_power_w:
                ratio = trainer_avg / pedal_avg
                if cfg.ratio_min < ratio < cfg.ratio_max:
                    self.ratio += (ratio - self.ratio) * cfg.ratio_alpha
            error = target - pedal_avg
            self.integral += cfg.ki * error
            self.integral = max(-cfg.max_offset_w, min(cfg.max_offset_w, self.integral))
        cmd = target * self.ratio + self.integral
        return max(target - cfg.max_offset_w, min(target + cfg.max_offset_w, cmd))


# ERG spiral guard: in ERG mode a falling cadence makes the trainer brake harder (it holds
# the power), which slows the rider further until they stall. Below SPIRAL_RELEASE_RPM for
# SPIRAL_RELEASE_S the trainer is let go (flat road) until the rider spins back up.
SPIRAL_RELEASE_RPM = 50
SPIRAL_RELEASE_S = 3.0
SPIRAL_RESUME_RPM = 70
SPIRAL_RESUME_S = 2.0
SPIRAL_RELEASE_SLOPE = 0.0  # what the trainer gets while released: a flat road


class SpiralGuard:
    """Decides when to let go of the ERG target because the cadence collapsed.

    It releases after ``SPIRAL_RELEASE_S`` below ``SPIRAL_RELEASE_RPM`` and resumes after
    ``SPIRAL_RESUME_S`` at ``SPIRAL_RESUME_RPM`` or more; the gap between the two keeps it
    from flipping back and forth. A missing cadence reading changes nothing.

    Attributes:
        released (bool): Whether the ERG target is currently let go.
    """

    def __init__(self) -> None:
        """Initialize the guard, not released."""
        self.released = False
        self._since: float | None = None  # when the cadence crossed the threshold that matters

    def reset(self) -> None:
        """Forget the cadence history and resume ERG."""
        self.released = False
        self._since = None

    def update(self, cadence: float | None, now: float) -> bool:
        """Feed the current cadence.

        Args:
            cadence (float | None): Cadence in rpm, ``None`` if unknown.
            now (float): Current monotonic time.

        Returns:
            bool: Whether the ERG target is let go.
        """
        if cadence is None:
            return self.released
        crossing = cadence >= SPIRAL_RESUME_RPM if self.released else cadence < SPIRAL_RELEASE_RPM
        if not crossing:
            self._since = None
            return self.released
        if self._since is None:
            self._since = now
        wait = SPIRAL_RESUME_S if self.released else SPIRAL_RELEASE_S
        if now - self._since >= wait:
            self.released = not self.released
            self._since = None
            log.info(
                "ERG %s (cadence %.0f rpm)", "released" if self.released else "resumed", cadence
            )
        return self.released


class TrainerController:
    """Holds the current mode/target and pushes changes to the trainer.

    In ERG mode an injected :class:`SpiralGuard` may replace the ERG command with a flat
    road while the cadence has collapsed; the mode and the target stay as they are. In slope
    mode an injected :class:`VirtualGearbox` turns the grade into the one that makes the
    trainer resist like that road ridden in the current virtual gear.

    Attributes:
        RESEND_S (float): Seconds after which an unchanged command is resent.
        mode (Mode): Current control mode.
        erg_target (float): ERG target power in watts.
        resistance_pct (float): Resistance level in percent.
        slope_pct (float): Simulated grade in percent.
        use_pedals (bool): Whether to match pedal power in ERG mode.
        matcher (PowerMatcher): Pedal power matcher.
        guard (SpiralGuard | None): Lets go of the ERG target when the cadence collapses;
            ``None`` when the spiral guard is off.
        gearbox (VirtualGearbox | None): Virtual gears applied in slope mode; ``None`` when
            virtual shifting is off.
        last_command (tuple[Mode, float] | None): Last command sent to the trainer.
        trainer (ControllableTrainer | None): Trainer being controlled.
    """

    RESEND_S = 10.0

    def __init__(
        self,
        cfg: PowerMatch,
        guard: SpiralGuard | None = None,
        gearbox: VirtualGearbox | None = None,
    ) -> None:
        """Initialize the controller.

        Args:
            cfg (PowerMatch): Pedal power matching parameters.
            guard (SpiralGuard | None): Spiral guard to apply in ERG mode; ``None``
                turns it off.
            gearbox (VirtualGearbox | None): Virtual gears to apply in slope mode; ``None``
                turns virtual shifting off.
        """
        self.mode = Mode.RESISTANCE
        self.erg_target = 150.0
        self.resistance_pct = 25.0
        self.slope_pct = 0.0
        self.use_pedals = True
        self.matcher = PowerMatcher(cfg)
        self.guard = guard
        self.gearbox = gearbox
        self.last_command: tuple[Mode, float] | None = None
        self._last_sent_at = 0.0
        self.trainer: ControllableTrainer | None = None

    def configure(self, assignment: Assignment, trainer: Device | None) -> None:
        """Adopt a new role assignment (e.g. after pairing or a power-source change).

        Args:
            assignment (Assignment): The resolved role assignment.
            trainer (Device | None): The trainer device, if one is assigned.
        """
        self.trainer = trainer if isinstance(trainer, ControllableTrainer) else None
        self.use_pedals = assignment.has_pedals
        self.force_resend()

    def set_mode(self, mode: Mode) -> None:
        """Change the control mode, resetting the power matcher on change.

        Args:
            mode (Mode): The new control mode.
        """
        if mode != self.mode:
            self.mode = mode
            self.matcher.reset()
            if self.guard:
                self.guard.reset()

    def set_spiral_guard(self, guard: SpiralGuard | None) -> None:
        """Replace the spiral guard; a release in progress ends with the old guard.

        Args:
            guard (SpiralGuard | None): The new guard; ``None`` turns it off.
        """
        self.guard = guard

    def set_gearbox(self, gearbox: VirtualGearbox | None) -> None:
        """Replace the virtual gearbox; a new one starts in the real gear.

        Args:
            gearbox (VirtualGearbox | None): The new gearbox; ``None`` turns virtual
                shifting off.
        """
        self.gearbox = gearbox

    @property
    def shifting(self) -> bool:
        """Whether shifting applies now: virtual shifting is on and the mode is slope.

        Returns:
            bool: ``True`` when :meth:`shift` changes the gear.
        """
        return self.gearbox is not None and self.mode == Mode.SLOPE

    def shift(self, steps: int) -> None:
        """Shift the virtual gear; does nothing unless :attr:`shifting`.

        Gears only exist in slope mode (see :mod:`smart_trainer.core.shifting`), so a shift
        in another mode is ignored rather than changing a gear the rider can't feel.

        Args:
            steps (int): Gears to move, e.g. ``1`` for one gear harder.
        """
        if self.gearbox is not None and self.shifting:
            self.gearbox.shift(steps)

    def set_target(self, mode: Mode, value: float) -> None:
        """Switch to a mode and set its target.

        Args:
            mode (Mode): The control mode.
            value (float): Its target: watts (ERG), percent (resistance) or grade (slope).
        """
        self.set_mode(mode)
        if mode == Mode.ERG:
            self.erg_target = value
        elif mode == Mode.RESISTANCE:
            self.resistance_pct = value
        else:
            self.slope_pct = value

    def target_for(self, mode: Mode) -> float:
        """The target kept for a mode, whether or not it is the current one.

        Args:
            mode (Mode): The control mode.

        Returns:
            float: Watts (ERG), percent (resistance) or grade (slope).
        """
        if mode == Mode.ERG:
            return self.erg_target
        if mode == Mode.RESISTANCE:
            return self.resistance_pct
        return self.slope_pct

    @property
    def erg_released(self) -> bool:
        """Whether the ERG target is let go because the cadence collapsed.

        Returns:
            bool: ``True`` while the spiral guard holds the trainer on a flat road.
        """
        return self.guard is not None and self.guard.released

    def force_resend(self) -> None:
        """Force the next :meth:`apply` call to resend the command."""
        self.last_command = None

    def compute(self, snapshot: Snapshot, now: float | None = None) -> tuple[Mode, float]:
        """Compute the command for the current mode.

        Args:
            snapshot (Snapshot): Current effective metrics.
            now (float | None): Current monotonic time; defaults to ``time.monotonic()``.

        Returns:
            tuple[Mode, float]: The mode and its value (watts, percent or grade).
        """
        if self.mode == Mode.ERG:
            if self._released(snapshot, now):
                return Mode.SLOPE, SPIRAL_RELEASE_SLOPE
            target = self.erg_target
            if self.use_pedals and snapshot.pedal_power is not None:
                return Mode.ERG, self.matcher.command(target, snapshot, now)
            return Mode.ERG, target
        if self.mode == Mode.RESISTANCE:
            return Mode.RESISTANCE, self.resistance_pct
        if self.gearbox is not None:
            return Mode.SLOPE, self.gearbox.grade_for(self.slope_pct, snapshot.speed_kmh)
        return Mode.SLOPE, self.slope_pct

    def _released(self, snapshot: Snapshot, now: float | None) -> bool:
        """Run the spiral guard; restart power matching cleanly when ERG resumes.

        Args:
            snapshot (Snapshot): Current effective metrics.
            now (float | None): Current monotonic time; defaults to ``time.monotonic()``.

        Returns:
            bool: Whether the ERG target is let go.
        """
        guard = self.guard
        if guard is None:
            return False
        was = guard.released
        released = guard.update(snapshot.cadence, now if now is not None else time.monotonic())
        if was and not released:
            # power was under target while grinding: drop the integral it built up
            self.matcher.reset()
        return released

    async def apply(
        self, snapshot: Snapshot, connected: bool, now: float | None = None
    ) -> tuple[Mode, float] | None:
        """Send the current command to the trainer if it changed or is due a resend.

        Args:
            snapshot (Snapshot): Current effective metrics.
            connected (bool): Whether the trainer is currently connected.
            now (float | None): Current monotonic time; defaults to ``time.monotonic()``.

        Returns:
            tuple[Mode, float] | None: The command sent, or ``None`` if nothing
            was sent or sending failed.
        """
        if self.trainer is None or not connected:
            self.last_command = None  # resend everything after reconnect
            return None
        now = now if now is not None else time.monotonic()
        mode, value = self.compute(snapshot, now)
        rounded = round(value) if mode == Mode.ERG else round(value, 1)
        changed = (
            self.last_command is None
            or self.last_command[0] != mode
            or abs(self.last_command[1] - rounded) >= (1 if mode == Mode.ERG else 0.1)
        )
        if not changed and now - self._last_sent_at < self.RESEND_S:
            return None
        try:
            if mode == Mode.ERG:
                await self.trainer.set_erg(rounded)
            elif mode == Mode.RESISTANCE:
                await self.trainer.set_resistance(rounded)
            else:
                await self.trainer.set_slope(rounded)
        except Exception as exc:
            log.warning("trainer command failed: %s", exc)
            self.last_command = None
            return None
        self.last_command = (mode, rounded)
        self._last_sent_at = now
        return self.last_command
