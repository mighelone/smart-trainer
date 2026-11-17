"""Discover BLE fitness devices and match them to drivers."""

from __future__ import annotations

from dataclasses import dataclass

from bleak import BleakScanner

# Import driver modules so they register themselves.
from smart_trainer.ble import cycling_power, ftms, heart_rate, uuids  # noqa: F401
from smart_trainer.ble.device import DRIVERS, Device, drivers_for_services

FITNESS_SERVICES = [
    uuids.FTMS_SERVICE,
    uuids.CYCLING_POWER_SERVICE,
    uuids.HEART_RATE_SERVICE,
    uuids.CSC_SERVICE,
]


@dataclass
class Discovered:
    """A fitness device found during a BLE scan.

    Attributes:
        address (str): Device address.
        name (str): Advertised device name.
        rssi (int): Signal strength in dBm.
        drivers (list[str]): Matching driver identifiers, best first.
    """

    address: str
    name: str
    rssi: int
    drivers: list[str]  # best first

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
    out: list[Discovered] = []
    for address, (dev, adv) in found.items():
        drivers = drivers_for_services(adv.service_uuids or [])
        if not drivers:
            continue
        out.append(
            Discovered(
                address=address,
                name=adv.local_name or dev.name or address,
                rssi=adv.rssi,
                drivers=[d.DRIVER for d in drivers],
            )
        )
    return sorted(out, key=lambda d: -d.rssi)


def make_device(driver: str, address: str, name: str = "") -> Device:
    """Instantiate a device using a registered driver.

    Args:
        driver (str): Driver identifier (see :data:`DRIVERS`).
        address (str): Device address.
        name (str): Human-readable device name.

    Returns:
        Device: The new device instance.
    """
    return DRIVERS[driver](address, name)
