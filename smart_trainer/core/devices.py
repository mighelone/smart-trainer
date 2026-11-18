"""Device registry: the active devices and the roles they are assigned to."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping

from smart_trainer.ble.device import ControllableTrainer, Device
from smart_trainer.config import PairedDevice, Settings
from smart_trainer.core.control import TrainerController
from smart_trainer.core.sources import SourceSelector
from smart_trainer.core.state import ConnState, Role


class DeviceHub(Mapping[str, Device]):
    """Active devices by id, plus their role assignment.

    The role assignment lives in the settings. Every change is pushed to the source
    selector (which device provides which metric) and to the trainer controller (which
    trainer to drive, and whether pedal power corrects it).

    Attributes:
        settings (Settings): User settings holding pairings and role assignments.
        selector (SourceSelector): Receives the readings and the assignment.
        controller (TrainerController): Receives the trainer to control.
    """

    def __init__(
        self, settings: Settings, selector: SourceSelector, controller: TrainerController
    ) -> None:
        """Initialize the hub and apply the saved role assignment.

        Args:
            settings (Settings): User settings.
            selector (SourceSelector): Source selector to feed.
            controller (TrainerController): Trainer controller to configure.
        """
        self.settings = settings
        self.selector = selector
        self.controller = controller
        self._devices: dict[str, Device] = {}
        self._apply_assignment()

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
        dev.subscribe(self.selector.on_reading)
        for role in roles or []:
            self.settings.role_assignment[role.value] = dev.id
        self._apply_assignment()
        dev.start()

    def pair(self, dev: Device, roles: list[Role]) -> None:
        """Pair a device: persist it in the settings and add it.

        Args:
            dev (Device): The device to pair.
            roles (list[Role]): Roles to assign to the device.
        """
        self.settings.devices[dev.id] = PairedDevice(
            address=dev.address, name=dev.name, driver=dev.DRIVER
        )
        self.add(dev, roles)
        self.settings.save()

    async def remove(self, device_id: str) -> None:
        """Stop and forget a device, removing its pairing and role assignments.

        Args:
            device_id (str): Id of the device to remove.
        """
        dev = self._devices.pop(device_id, None)
        if dev:
            await dev.stop()
        self.settings.devices.pop(device_id, None)
        for role, did in list(self.settings.role_assignment.items()):
            if did == device_id:
                del self.settings.role_assignment[role]
        self._apply_assignment()
        self.settings.save()

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
            self.settings.role_assignment[role.value] = device_id
        self._apply_assignment()
        self.settings.save()

    def set_power_source(self, source: str) -> None:
        """Choose the preferred power source and persist it.

        Args:
            source (str): ``"pedals"`` or ``"trainer"``.
        """
        self.settings.power_source = source
        self.settings.save()
        self._apply_assignment()

    def _apply_assignment(self) -> None:
        """Push the role assignment from the settings to the selector and controller."""
        ra = {Role(k): v for k, v in self.settings.role_assignment.items()}
        trainer_id = ra.get(Role.TRAINER)
        if self.settings.power_source == "trainer" and trainer_id:
            ra[Role.POWER] = trainer_id
        self.selector.assignment = ra
        self.selector.trainer_id = trainer_id
        dev = self._devices.get(trainer_id) if trainer_id else None
        self.controller.trainer = dev if isinstance(dev, ControllableTrainer) else None
        self.controller.use_pedals = self.settings.power_source == "pedals" and ra.get(
            Role.POWER
        ) not in (None, trainer_id)
        self.controller.force_resend()

    # ------------------------------------------------------------------ queries
    @property
    def trainer_id(self) -> str | None:
        """Id of the device assigned as trainer.

        Returns:
            str | None: The trainer id, or ``None``.
        """
        return self.selector.trainer_id

    @property
    def power_id(self) -> str | None:
        """Id of the device providing power (pedals, or the trainer itself).

        Returns:
            str | None: The power source id, or ``None``.
        """
        return self.selector.assignment.get(Role.POWER)

    def device_for(self, role: Role) -> Device | None:
        """Get the device assigned to a role.

        Args:
            role (Role): The role to look up.

        Returns:
            Device | None: The assigned device, or ``None``.
        """
        did = self.selector.assignment.get(role)
        return self._devices.get(did) if did else None

    def trainer_connected(self) -> bool:
        """Whether the assigned trainer is connected.

        Returns:
            bool: ``True`` if a trainer is assigned and connected.
        """
        device = self.device_for(Role.TRAINER)
        return bool(device and device.state == ConnState.CONNECTED)
