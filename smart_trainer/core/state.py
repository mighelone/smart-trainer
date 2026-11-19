"""Shared data types for live readings."""

from __future__ import annotations

import time
from collections.abc import Mapping
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


@dataclass(frozen=True, slots=True)
class Assignment:
    """Resolved role assignment: which device plays which role.

    "Resolved" means the power-source preference is already applied: when the rider
    prefers trainer power, :attr:`power_id` is the trainer.

    Attributes:
        roles (Mapping[Role, str]): Device id for each assigned role.
    """

    roles: Mapping[Role, str] = field(default_factory=dict)

    def get(self, role: Role) -> str | None:
        """Get the device assigned to a role.

        Args:
            role (Role): The role to look up.

        Returns:
            str | None: The device id, or ``None`` if the role is unassigned.
        """
        return self.roles.get(role)

    @property
    def trainer_id(self) -> str | None:
        """Id of the trainer, if any.

        Returns:
            str | None: The trainer id.
        """
        return self.roles.get(Role.TRAINER)

    @property
    def power_id(self) -> str | None:
        """Id of the preferred power source (pedals, or the trainer itself).

        Returns:
            str | None: The power source id.
        """
        return self.roles.get(Role.POWER)

    @property
    def has_pedals(self) -> bool:
        """Whether power comes from a device other than the trainer (e.g. pedals).

        Returns:
            bool: ``True`` if a separate power meter is the preferred power source.
        """
        return self.power_id is not None and self.power_id != self.trainer_id


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


@dataclass(slots=True, frozen=True)
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
        power_mean (float | None): Mean of the ``power_source`` readings since the
            previous snapshot (one engine tick, so the 1 s mean): what is recorded.
            ``power`` (the latest reading) when none arrived in that time.
        power_avg (float | None): 3 s average of ``power`` from ``power_source``.
        pedal_avg (float | None): 3 s average of the pedal power.
        trainer_avg (float | None): 3 s average of the trainer power.
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
    power_mean: float | None = None
    power_avg: float | None = None
    pedal_avg: float | None = None
    trainer_avg: float | None = None
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Without a mean (e.g. built by hand), the latest reading stands for the second."""
        if self.power_mean is None:
            object.__setattr__(self, "power_mean", self.power)  # frozen: set it once here
