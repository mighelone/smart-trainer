"""Trainer control modes and pedal power matching."""

from __future__ import annotations

import logging
import time
from enum import StrEnum

from smart_trainer.ble.device import ControllableTrainer
from smart_trainer.config import PowerMatch
from smart_trainer.core.state import Snapshot

log = logging.getLogger(__name__)


class Mode(StrEnum):
    ERG = "erg"
    RESISTANCE = "resistance"
    SLOPE = "slope"


class PowerMatcher:
    """Makes the *pedal* power hit the ERG target while the trainer closes its own loop.

    1. Learns the ratio trainer_power / pedal_power (EMA, only while riding steadily) and
       commands `target * ratio`. This handles a proportional calibration difference
       instantly on target changes.
    2. A slow integral term removes any residual pedal error (e.g. trainers whose ERG loop
       doesn't track their own reported power exactly).
    The total correction is clamped to ±max_offset_w.
    """

    def __init__(self, cfg: PowerMatch) -> None:
        self.cfg = cfg
        self.ratio = 1.0
        self.integral = 0.0
        self._last_target: float | None = None
        self._target_changed_at = 0.0

    def reset(self) -> None:
        self.integral = 0.0
        self._last_target = None

    def command(
        self,
        target: float,
        snap: Snapshot,
        pedal_avg: float | None,
        trainer_avg: float | None,
        now: float | None = None,
    ) -> float:
        now = now if now is not None else time.monotonic()
        c = self.cfg
        if self._last_target is None or abs(target - self._last_target) >= 1:
            self._last_target = target
            self._target_changed_at = now
        settled = now - self._target_changed_at >= c.settle_s
        riding = (snap.cadence or 0) >= c.min_cadence
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
    """Holds the current mode/target and pushes changes to the trainer."""

    RESEND_S = 10.0

    def __init__(self, cfg: PowerMatch) -> None:
        self.mode = Mode.RESISTANCE
        self.erg_target = 150.0
        self.resistance_pct = 25.0
        self.slope_pct = 0.0
        self.use_pedals = True
        self.matcher = PowerMatcher(cfg)
        self.last_command: tuple[Mode, float] | None = None
        self._last_sent_at = 0.0
        self.trainer: ControllableTrainer | None = None

    def set_mode(self, mode: Mode) -> None:
        if mode != self.mode:
            self.mode = mode
            self.matcher.reset()

    def force_resend(self) -> None:
        self.last_command = None

    def compute(
        self,
        snap: Snapshot,
        pedal_avg: float | None,
        trainer_avg: float | None,
        now: float | None = None,
    ) -> tuple[Mode, float]:
        if self.mode == Mode.ERG:
            target = self.erg_target
            if self.use_pedals and snap.pedal_power is not None:
                return Mode.ERG, self.matcher.command(target, snap, pedal_avg, trainer_avg, now)
            return Mode.ERG, target
        if self.mode == Mode.RESISTANCE:
            return Mode.RESISTANCE, self.resistance_pct
        return Mode.SLOPE, self.slope_pct

    async def apply(
        self,
        snap: Snapshot,
        pedal_avg: float | None,
        trainer_avg: float | None,
        connected: bool,
        now: float | None = None,
    ) -> tuple[Mode, float] | None:
        if self.trainer is None or not connected:
            self.last_command = None  # resend everything after reconnect
            return None
        now = now if now is not None else time.monotonic()
        mode, value = self.compute(snap, pedal_avg, trainer_avg, now)
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
