"""Shared data types for live readings."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    """Kind of data a device can provide."""

    TRAINER = "trainer"  # controllable resistance unit
    POWER = "power"
    CADENCE = "cadence"
    SPEED = "speed"
    HR = "hr"


class ConnState(StrEnum):
    """Device connection state."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


@dataclass(slots=True)
class Reading:
    """A partial update from one device. Only non-None fields are meaningful.

    Attributes:
        device_id (str): Id of the device that produced the reading.
        power (float | None): Power in watts.
        cadence (float | None): Cadence in rpm.
        speed_kmh (float | None): Speed in km/h.
        hr (int | None): Heart rate in bpm.
        balance_left (float | None): Left pedal power balance in percent.
        ts (float): Monotonic timestamp of the reading.
    """

    device_id: str
    power: float | None = None
    cadence: float | None = None
    speed_kmh: float | None = None
    hr: int | None = None
    balance_left: float | None = None
    ts: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class Snapshot:
    """Effective metrics for one second, after source selection.

    Attributes:
        power (float | None): Effective power in watts.
        cadence (float | None): Cadence in rpm.
        speed_kmh (float | None): Speed in km/h.
        hr (int | None): Heart rate in bpm.
        balance_left (float | None): Left pedal power balance in percent.
        trainer_power (float | None): Power reported by the trainer in watts.
        pedal_power (float | None): Power reported by the pedals in watts.
        power_source (str | None): Device id actually used for ``power``.
        warnings (list[str]): User-facing warnings for this second.
    """

    power: float | None = None
    cadence: float | None = None
    speed_kmh: float | None = None
    hr: int | None = None
    balance_left: float | None = None
    trainer_power: float | None = None
    pedal_power: float | None = None
    power_source: str | None = None  # device id actually used for `power`
    warnings: list[str] = field(default_factory=list)
