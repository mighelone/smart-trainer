"""Combine readings from all devices into one effective Snapshot per second."""
from __future__ import annotations

import time
from collections import deque

from smart_trainer.core.state import Reading, Role, Snapshot

STALE_S = 3.0


class _Latest:
    __slots__ = ("value", "ts")

    def __init__(self) -> None:
        self.value: float | None = None
        self.ts = 0.0

    def set(self, v: float | None, ts: float) -> None:
        if v is not None:
            self.value, self.ts = v, ts

    def get(self, now: float) -> float | None:
        return self.value if self.value is not None and now - self.ts <= STALE_S else None


class SourceSelector:
    """Keeps the latest value per (device, metric) and resolves roles to devices.

    `assignment` maps a Role to the preferred device id. Power falls back to the trainer
    when the preferred power device has gone silent.
    """

    METRICS = ("power", "cadence", "speed_kmh", "hr", "balance_left")

    def __init__(self, assignment: dict[Role, str] | None = None, trainer_id: str | None = None):
        self.assignment: dict[Role, str] = dict(assignment or {})
        self.trainer_id = trainer_id
        self._latest: dict[tuple[str, str], _Latest] = {}
        self._power_hist: dict[str, deque[tuple[float, float]]] = {}

    def on_reading(self, r: Reading) -> None:
        for m in self.METRICS:
            v = getattr(r, m)
            if v is not None:
                self._latest.setdefault((r.device_id, m), _Latest()).set(v, r.ts)
        if r.power is not None:
            self._power_hist.setdefault(r.device_id, deque(maxlen=30)).append((r.ts, r.power))

    def value(self, device_id: str | None, metric: str, now: float | None = None):
        if device_id is None:
            return None
        lat = self._latest.get((device_id, metric))
        return lat.get(now if now is not None else time.monotonic()) if lat else None

    def avg_power(self, device_id: str | None, window_s: float = 3.0, now: float | None = None):
        if device_id is None or device_id not in self._power_hist:
            return None
        now = now if now is not None else time.monotonic()
        vals = [p for ts, p in self._power_hist[device_id] if now - ts <= window_s]
        return sum(vals) / len(vals) if vals else None

    def _first(self, metric: str, candidates: list[str | None], now: float):
        for dev in candidates:
            v = self.value(dev, metric, now)
            if v is not None:
                return v, dev
        return None, None

    def snapshot(self, now: float | None = None) -> Snapshot:
        now = now if now is not None else time.monotonic()
        a = self.assignment
        snap = Snapshot()
        pref_power = a.get(Role.POWER)
        trainer = self.trainer_id
        snap.trainer_power = self.value(trainer, "power", now)
        snap.pedal_power = self.value(pref_power, "power", now) if pref_power != trainer else None
        snap.power, snap.power_source = self._first("power", [pref_power, trainer], now)
        if pref_power and pref_power != trainer and snap.power_source == trainer:
            snap.warnings.append("Pedals not sending power: using trainer power")
        snap.cadence, _ = self._first("cadence", [a.get(Role.CADENCE), pref_power, trainer], now)
        snap.speed_kmh, _ = self._first("speed_kmh", [a.get(Role.SPEED), trainer], now)
        hr, _ = self._first("hr", [a.get(Role.HR), trainer], now)
        snap.hr = int(hr) if hr is not None else None
        snap.balance_left = self.value(pref_power, "balance_left", now)
        return snap
