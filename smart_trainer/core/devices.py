"""Device registry: the active devices and the roles they are assigned to."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Mapping
from typing import Protocol

from smart_trainer.ble.device import Device, ReadingCallback
from smart_trainer.config import PairedDevice
from smart_trainer.core.state import Assignment, ConnState, Role

AssignmentListener = Callable[[Assignment, Device | None], None]
"""Notified with the new assignment and the trainer device (if any) on every change."""


class DeviceConfig(Protocol):
    """The part of the settings the device hub reads and persists.

    :class:`~smart_trainer.config.Settings` satisfies it structurally.

    Attributes:
        devices (dict[str, PairedDevice]): Paired devices by id.
        role_assignment (dict[str, str]): Device id by role name.
        power_source (str): ``"pedals"`` or ``"trainer"``: preferred power source.
    """

    devices: dict[str, PairedDevice]
    role_assignment: dict[str, str]
    power_source: str

    def save(self) -> None:
        """Persist the configuration."""
        ...


class DeviceHub(Mapping[str, Device]):
    """Active devices by id, plus their role assignment.

    The hub is the single owner of the role assignment: it is saved in its
    :class:`DeviceConfig` and published as an immutable :attr:`assignment`. The hub
    knows nothing about who consumes readings or assignments; it only calls the
    callbacks it is given.
    """

    def __init__(
        self,
        config: DeviceConfig,
        on_reading: ReadingCallback,
        on_change: AssignmentListener = lambda assignment, trainer: None,
    ) -> None:
        """Initialize the hub and apply the saved role assignment.

        Args:
            config (DeviceConfig): Pairings, role assignment and power source, persisted
                on every change.
            on_reading (ReadingCallback): Subscribed to every added device's readings.
            on_change (AssignmentListener): Called whenever the assignment changes
                (including once now, with the saved assignment).
        """
        self._config = config
        self._on_reading = on_reading
        self._on_change = on_change
        self._devices: dict[str, Device] = {}
        self._notify()

    # ------------------------------------------------------------- Mapping API
    def __getitem__(self, device_id: str) -> Device:
        """Get a device by id.

        Args:
            device_id (str): Device id.

        Returns:
            Device: The device.
        """
        return self._devices[device_id]

    def __iter__(self) -> Iterator[str]:
        """Iterate over the device ids.

        Returns:
            Iterator[str]: Device ids.
        """
        return iter(self._devices)

    def __len__(self) -> int:
        """Count the devices.

        Returns:
            int: Number of active devices.
        """
        return len(self._devices)

    # ---------------------------------------------------------------- lifecycle
    def add(self, dev: Device, roles: list[Role] | None = None) -> None:
        """Add (or replace) a device, assign it roles and start it.

        Args:
            dev (Device): The device to add.
            roles (list[Role] | None): Roles to assign to the device.
        """
        old = self._devices.get(dev.id)
        if old is not None:
            asyncio.ensure_future(old.stop())
        self._devices[dev.id] = dev
        dev.subscribe(self._on_reading)
        for role in roles or []:
            self._config.role_assignment[role.value] = dev.id
        self._notify()
        dev.start()

    def pair(self, dev: Device, roles: list[Role]) -> None:
        """Pair a device: persist it in the config and add it.

        Args:
            dev (Device): The device to pair.
            roles (list[Role]): Roles to assign to the device.
        """
        self._config.devices[dev.id] = PairedDevice(
            address=dev.address, name=dev.name, driver=dev.DRIVER
        )
        self.add(dev, roles)
        self._config.save()

    async def remove(self, device_id: str) -> None:
        """Stop and forget a device, removing its pairing and role assignments.

        Args:
            device_id (str): Id of the device to remove.
        """
        dev = self._devices.pop(device_id, None)
        if dev:
            await dev.stop()
        self._config.devices.pop(device_id, None)
        for role, did in list(self._config.role_assignment.items()):
            if did == device_id:
                del self._config.role_assignment[role]
        self._notify()
        self._config.save()

    async def stop_all(self) -> None:
        """Stop every device."""
        for dev in list(self._devices.values()):
            await dev.stop()

    # -------------------------------------------------------------- assignment
    def assign(self, device_id: str, roles: list[Role]) -> None:
        """Assign roles to an already added device and persist them.

        Args:
            device_id (str): Id of the device.
            roles (list[Role]): Roles to assign to it.
        """
        for role in roles:
            self._config.role_assignment[role.value] = device_id
        self._notify()
        self._config.save()

    def set_power_source(self, source: str) -> None:
        """Choose the preferred power source and persist it.

        Args:
            source (str): ``"pedals"`` or ``"trainer"``.
        """
        self._config.power_source = source
        self._config.save()
        self._notify()

    def _notify(self) -> None:
        """Tell the listener the assignment (or the trainer device) may have changed."""
        self._on_change(self.assignment, self.device_for(Role.TRAINER))

    # ------------------------------------------------------------------ queries
    @property
    def assignment(self) -> Assignment:
        """The effective role assignment, derived from the config on every access.

        When the rider prefers trainer power (and a trainer is assigned), the trainer
        also takes the power role.

        Returns:
            Assignment: Roles by device id, with the power-source preference applied.
        """
        roles = {Role(k): v for k, v in self._config.role_assignment.items()}
        trainer_id = roles.get(Role.TRAINER)
        if self._config.power_source == "trainer" and trainer_id:
            roles[Role.POWER] = trainer_id
        return Assignment(roles)

    def device_for(self, role: Role) -> Device | None:
        """Get the device assigned to a role.

        Args:
            role (Role): The role to look up.

        Returns:
            Device | None: The assigned device, or ``None``.
        """
        did = self.assignment.get(role)
        return self._devices.get(did) if did else None

    def trainer_connected(self) -> bool:
        """Whether the assigned trainer is connected.

        Returns:
            bool: ``True`` if a trainer is assigned and connected.
        """
        device = self.device_for(Role.TRAINER)
        return bool(device and device.state == ConnState.CONNECTED)
