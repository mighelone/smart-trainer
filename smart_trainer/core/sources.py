"""Combine readings from all devices into one effective Snapshot per second."""

from __future__ import annotations

import time
from collections import deque

from smart_trainer.core.state import Reading, Role, Snapshot

STALE_S = 3.0


class _Latest:
    """Latest value of a metric and the time it was received.

    Attributes:
        value (float | None): Last received value.
        ts (float): Monotonic timestamp of ``value``.
    """

    __slots__ = ("value", "ts")

    def __init__(self) -> None:
        """Initialize with no value."""
        self.value: float | None = None
        self.ts = 0.0

    def set(self, v: float | None, ts: float) -> None:
        """Store a new value, ignoring ``None``.

        Args:
            v (float | None): The new value.
            ts (float): Monotonic timestamp of the value.
        """
        if v is not None:
            self.value, self.ts = v, ts

    def get(self, now: float) -> float | None:
        """Return the value if it is not stale.

        Args:
            now (float): Current monotonic time.

        Returns:
            float | None: The value, or ``None`` if missing or older than ``STALE_S``.
        """
        return self.value if self.value is not None and now - self.ts <= STALE_S else None


class SourceSelector:
    """Keeps the latest value per (device, metric) and resolves roles to devices.

    ``assignment`` maps a Role to the preferred device id. Power falls back to the
    trainer when the preferred power device has gone silent.

    Attributes:
        METRICS (tuple[str, ...]): Reading fields tracked per device.
        assignment (dict[Role, str]): Preferred device id for each role.
        trainer_id (str | None): Id of the trainer device, if any.
    """

    METRICS = ("power", "cadence", "speed_kmh", "hr", "balance_left")

    def __init__(self, assignment: dict[Role, str] | None = None, trainer_id: str | None = None):
        """Initialize the selector.

        Args:
            assignment (dict[Role, str] | None): Preferred device id for each role.
            trainer_id (str | None): Id of the trainer device, if any.
        """
        self.assignment: dict[Role, str] = dict(assignment or {})
        self.trainer_id = trainer_id
        self._latest: dict[tuple[str, str], _Latest] = {}
        self._power_hist: dict[str, deque[tuple[float, float]]] = {}

    def on_reading(self, r: Reading) -> None:
        """Record the metrics contained in a reading.

        Args:
            r (Reading): The incoming reading.
        """
        for m in self.METRICS:
            v = getattr(r, m)
            if v is not None:
                self._latest.setdefault((r.device_id, m), _Latest()).set(v, r.ts)
        if r.power is not None:
            self._power_hist.setdefault(r.device_id, deque(maxlen=30)).append((r.ts, r.power))

    def value(self, device_id: str | None, metric: str, now: float | None = None):
        """Get the latest non-stale value of a metric for a device.

        Args:
            device_id (str | None): Device id; ``None`` always yields ``None``.
            metric (str): Metric name (one of :attr:`METRICS`).
            now (float | None): Current monotonic time; defaults to ``time.monotonic()``.

        Returns:
            float | None: The value, or ``None`` if missing or stale.
        """
        if device_id is None:
            return None
        lat = self._latest.get((device_id, metric))
        return lat.get(now if now is not None else time.monotonic()) if lat else None

    def avg_power(self, device_id: str | None, window_s: float = 3.0, now: float | None = None):
        """Average power of a device over a recent time window.

        Args:
            device_id (str | None): Device id; ``None`` always yields ``None``.
            window_s (float): Averaging window in seconds.
            now (float | None): Current monotonic time; defaults to ``time.monotonic()``.

        Returns:
            float | None: Average power in watts, or ``None`` if no samples in window.
        """
        if device_id is None or device_id not in self._power_hist:
            return None
        now = now if now is not None else time.monotonic()
        vals = [p for ts, p in self._power_hist[device_id] if now - ts <= window_s]
        return sum(vals) / len(vals) if vals else None

    def _first(self, metric: str, candidates: list[str | None], now: float):
        """Return the first non-stale value of a metric among candidate devices.

        Args:
            metric (str): Metric name.
            candidates (list[str | None]): Device ids in order of preference.
            now (float): Current monotonic time.

        Returns:
            tuple[float | None, str | None]: The value and the device it came from,
            or ``(None, None)`` if no candidate has a value.
        """
        for dev in candidates:
            v = self.value(dev, metric, now)
            if v is not None:
                return v, dev
        return None, None

    def snapshot(self, now: float | None = None) -> Snapshot:
        """Build the effective metrics snapshot for the current second.

        Args:
            now (float | None): Current monotonic time; defaults to ``time.monotonic()``.

        Returns:
            Snapshot: The resolved metrics, including fallback warnings.
        """
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
