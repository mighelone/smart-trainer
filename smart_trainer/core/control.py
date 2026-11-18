"""Trainer control modes and pedal power matching."""

from __future__ import annotations

import logging
import time
from enum import StrEnum

from smart_trainer.ble.device import ControllableTrainer, Device
from smart_trainer.config import PowerMatch
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
        c = self.cfg
        if self._last_target is None or abs(target - self._last_target) >= 1:
            self._last_target = target
            self._target_changed_at = now
        settled = now - self._target_changed_at >= c.settle_s
        riding = (snapshot.cadence or 0) >= c.min_cadence
        pedal_avg, trainer_avg = snapshot.pedal_avg, snapshot.trainer_avg
        if settled and riding and pedal_avg and pedal_avg > 30:
            if trainer_avg and trainer_avg > 30:
                r = trainer_avg / pedal_avg
                if 0.7 < r < 1.3:
                    self.ratio += (r - self.ratio) * c.ratio_alpha
            error = target - pedal_avg
            self.integral += c.ki * error
            self.integral = max(-c.max_offset_w, min(c.max_offset_w, self.integral))
        cmd = target * self.ratio + self.integral
        return max(target - c.max_offset_w, min(target + c.max_offset_w, cmd))


class TrainerController:
    """Holds the current mode/target and pushes changes to the trainer.

    Attributes:
        RESEND_S (float): Seconds after which an unchanged command is resent.
        mode (Mode): Current control mode.
        erg_target (float): ERG target power in watts.
        resistance_pct (float): Resistance level in percent.
        slope_pct (float): Simulated grade in percent.
        use_pedals (bool): Whether to match pedal power in ERG mode.
        matcher (PowerMatcher): Pedal power matcher.
        last_command (tuple[Mode, float] | None): Last command sent to the trainer.
        trainer (ControllableTrainer | None): Trainer being controlled.
    """

    RESEND_S = 10.0

    def __init__(self, cfg: PowerMatch) -> None:
        """Initialize the controller.

        Args:
            cfg (PowerMatch): Pedal power matching parameters.
        """
        self.mode = Mode.RESISTANCE
        self.erg_target = 150.0
        self.resistance_pct = 25.0
        self.slope_pct = 0.0
        self.use_pedals = True
        self.matcher = PowerMatcher(cfg)
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
            target = self.erg_target
            if self.use_pedals and snapshot.pedal_power is not None:
                return Mode.ERG, self.matcher.command(target, snapshot, now)
            return Mode.ERG, target
        if self.mode == Mode.RESISTANCE:
            return Mode.RESISTANCE, self.resistance_pct
        return Mode.SLOPE, self.slope_pct

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
