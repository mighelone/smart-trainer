"""Discover BLE fitness devices and match them to drivers."""

from __future__ import annotations

from dataclasses import dataclass

from bleak import BleakScanner

# Import driver modules so they register themselves.
from smart_trainer.ble import cycling_power, ftms, heart_rate, uuids, zwift_click  # noqa: F401
from smart_trainer.ble.device import DRIVERS, Device, DriverId, drivers_for_services
from smart_trainer.ble.parsers.zwift import zwift_device_type

FITNESS_SERVICES = [
    uuids.FTMS_SERVICE,
    uuids.CYCLING_POWER_SERVICE,
    uuids.HEART_RATE_SERVICE,
    uuids.CSC_SERVICE,
    uuids.ZWIFT_SERVICE,
]


@dataclass
class Discovered:
    """A fitness device found during a BLE scan.

    Attributes:
        address (str): Device address.
        name (str): Advertised device name.
        rssi (int): Signal strength in dBm.
        drivers (list[DriverId]): Matching driver identifiers, best first.
        zwift_type (int | None): Zwift device type from the manufacturer data, ``None`` for
            other devices.
    """

    address: str
    name: str
    rssi: int
    drivers: list[DriverId]  # best first
    zwift_type: int | None = None

    @property
    def roles(self) -> set[str]:
        """Roles the device can provide across all matching drivers.

        Returns:
            set[str]: Role values (e.g. ``"power"``, ``"hr"``).
        """
        return {r.value for d in self.drivers for r in DRIVERS[d].ROLES}


async def scan(timeout: float = 6.0) -> list[Discovered]:
    """Scan for BLE fitness devices and match them to drivers.

    Args:
        timeout (float): Scan duration in seconds.

    Returns:
        list[Discovered]: Devices with at least one matching driver,
        strongest signal first.
    """
    found = await BleakScanner.discover(
        timeout=timeout, return_adv=True, service_uuids=FITNESS_SERVICES
    )
    out = [
        discovered(
            address,
            adv.local_name or dev.name or address,
            adv.rssi,
            adv.service_uuids or [],
            adv.manufacturer_data or {},
        )
        for address, (dev, adv) in found.items()
    ]
    return sorted((d for d in out if d is not None), key=lambda d: -d.rssi)


def discovered(
    address: str,
    name: str,
    rssi: int,
    service_uuids: list[str],
    manufacturer_data: dict[int, bytes],
) -> Discovered | None:
    """Match one advertisement to the drivers that can handle it.

    Args:
        address (str): Device address.
        name (str): Advertised device name.
        rssi (int): Signal strength in dBm.
        service_uuids (list[str]): Advertised service UUIDs.
        manufacturer_data (dict[int, bytes]): Manufacturer data by company ID.

    Returns:
        Discovered | None: The device, or ``None`` if no driver handles it.
    """
    drivers = drivers_for_services(service_uuids)
    if not drivers:
        return None
    return Discovered(
        address, name, rssi, [d.DRIVER for d in drivers], zwift_device_type(manufacturer_data)
    )


def make_device(driver: DriverId | str, address: str, name: str = "") -> Device:
    """Instantiate a device using a registered driver.

    Args:
        driver (DriverId | str): Driver identifier (see :data:`DRIVERS`).
        address (str): Device address.
        name (str): Human-readable device name.

    Returns:
        Device: The new device instance.

    Raises:
        KeyError: If no driver is registered under ``driver``.
    """
    try:
        driver_id = DriverId(driver)
    except ValueError:
        raise KeyError(driver) from None
    return DRIVERS[driver_id](address, name)
