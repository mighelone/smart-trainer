"""Shared data types for live readings."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    TRAINER = "trainer"  # controllable resistance unit
    POWER = "power"
    CADENCE = "cadence"
    SPEED = "speed"
    HR = "hr"


class ConnState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


@dataclass(slots=True)
class Reading:
    """A partial update from one device. Only non-None fields are meaningful."""

    device_id: str
    power: float | None = None
    cadence: float | None = None
    speed_kmh: float | None = None
    hr: int | None = None
    balance_left: float | None = None
    ts: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class Snapshot:
    """Effective metrics for one second, after source selection."""

    power: float | None = None
    cadence: float | None = None
    speed_kmh: float | None = None
    hr: int | None = None
    balance_left: float | None = None
    trainer_power: float | None = None
    pedal_power: float | None = None
    power_source: str | None = None  # device id actually used for `power`
    warnings: list[str] = field(default_factory=list)
