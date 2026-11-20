"""Combine readings from all devices into one effective Snapshot per second."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import ClassVar

from smart_trainer.core.state import Assignment, Reading, Role, Snapshot

STALE_S = 3.0


@dataclass(slots=True)
class _Latest:
    """Latest value of a metric and the time it was received.

    Attributes:
        value (float | None): Last received value.
        ts (float): Monotonic timestamp of ``value``.
    """

    value: float | None = None
    ts: float = 0.0

    def set(self, value: float | None, ts: float) -> None:
        """Store a new value, ignoring ``None``.

        Args:
            value (float | None): The new value.
            ts (float): Monotonic timestamp of the value.
        """
        if value is not None:
            self.value, self.ts = value, ts

    def get(self, now: float) -> float | None:
        """Return the value if it is not stale.

        Args:
            now (float): Current monotonic time.

        Returns:
            float | None: The value, or ``None`` if missing or older than ``STALE_S``.
        """
        return self.value if self.value is not None and now - self.ts <= STALE_S else None


@dataclass(eq=False)
class SourceSelector:
    """Keeps the latest value per (device, metric) and builds snapshots from them.

    It only stores readings; which device plays which role is passed to
    :meth:`create_snapshot` as an :class:`Assignment`. Power falls back to the trainer
    when the preferred power device has gone silent. ``eq=False`` keeps identity
    semantics: the selector is stateful, not a value.

    Attributes:
        METRICS (tuple[str, ...]): Reading fields tracked per device.
        AVG_WINDOW_S (float): Window of the power averages, in seconds.
    """

    METRICS: ClassVar[tuple[str, ...]] = ("power", "cadence", "speed_kmh", "hr", "balance_left")
    AVG_WINDOW_S: ClassVar[float] = 3.0

    _latest: dict[tuple[str, str], _Latest] = field(default_factory=dict, init=False, repr=False)
    _power_hist: dict[str, deque[tuple[float, float]]] = field(
        default_factory=dict, init=False, repr=False
    )
    _last_snapshot_at: float | None = field(default=None, init=False, repr=False)

    def on_reading(self, reading: Reading) -> None:
        """Record the metrics contained in a reading.

        Args:
            reading (Reading): The incoming reading.
        """
        for metric in self.METRICS:
            value = getattr(reading, metric)
            if value is not None:
                self._latest.setdefault((reading.device_id, metric), _Latest()).set(
                    value, reading.ts
                )
        if reading.power is not None:
            self._power_hist.setdefault(reading.device_id, deque(maxlen=30)).append(
                (reading.ts, reading.power)
            )

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

    def _avg_power(self, device_id: str | None, now: float) -> float | None:
        """Average power of a device over the last :attr:`AVG_WINDOW_S` seconds.

        Args:
            device_id (str | None): Device id; ``None`` always yields ``None``.
            now (float): Current monotonic time.

        Returns:
            float | None: Average power in watts, or ``None`` if no samples in window.
        """
        if device_id is None or device_id not in self._power_hist:
            return None
        vals = [p for ts, p in self._power_hist[device_id] if now - ts <= self.AVG_WINDOW_S]
        return sum(vals) / len(vals) if vals else None

    def _mean_power_since(self, device_id: str | None, since: float, now: float) -> float | None:
        """Mean power of a device over the readings received in ``(since, now]``.

        Args:
            device_id (str | None): Device id; ``None`` always yields ``None``.
            since (float): Start of the window (exclusive), monotonic time.
            now (float): End of the window (inclusive), monotonic time.

        Returns:
            float | None: Mean power in watts, or ``None`` if no reading in the window.
        """
        if device_id is None or device_id not in self._power_hist:
            return None
        vals = [p for ts, p in self._power_hist[device_id] if since < ts <= now]
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
            value = self.value(dev, metric, now)
            if value is not None:
                return value, dev
        return None, None

    def create_snapshot(self, assignment: Assignment, now: float | None = None) -> Snapshot:
        """Build the effective metrics snapshot for the current second.

        Args:
            assignment (Assignment): Which device plays which role.
            now (float | None): Current monotonic time; defaults to ``time.monotonic()``.

        Returns:
            Snapshot: The resolved metrics, including power averages and fallback warnings.
        """
        now = now if now is not None else time.monotonic()
        since = self._last_snapshot_at if self._last_snapshot_at is not None else now - 1.0
        self._last_snapshot_at = now
        pedals = assignment.power_id if assignment.has_pedals else None
        trainer = assignment.trainer_id
        power_candidates = [assignment.power_id, trainer]
        power, power_source = self._first("power", power_candidates, now)
        cadence, _ = self._first("cadence", [assignment.get(Role.CADENCE), *power_candidates], now)
        speed_kmh, _ = self._first("speed_kmh", [assignment.get(Role.SPEED), trainer], now)
        hr, _ = self._first("hr", [assignment.get(Role.HR), trainer], now)
        warnings = []
        if pedals and power_source == trainer:
            warnings.append("Pedals not sending power: using trainer power")
        return Snapshot(
            power=power,
            cadence=cadence,
            speed_kmh=speed_kmh,
            hr=int(hr) if hr is not None else None,
            balance_left=self.value(assignment.power_id, "balance_left", now),
            trainer_power=self.value(trainer, "power", now),
            pedal_power=self.value(pedals, "power", now),
            power_source=power_source,
            power_mean=self._mean_power_since(power_source, since, now),  # else: power
            power_avg=self._avg_power(power_source, now),
            pedal_avg=self._avg_power(pedals, now),
            trainer_avg=self._avg_power(trainer, now),
            warnings=warnings,
        )
