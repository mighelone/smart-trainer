"""Discover BLE fitness devices and match them to drivers."""
from __future__ import annotations

from dataclasses import dataclass

from bleak import BleakScanner

from smart_trainer.ble import uuids
# Import driver modules so they register themselves.
from smart_trainer.ble import cycling_power, ftms, heart_rate  # noqa: F401
from smart_trainer.ble.device import DRIVERS, Device, drivers_for_services

FITNESS_SERVICES = [uuids.FTMS_SERVICE, uuids.CYCLING_POWER_SERVICE, uuids.HEART_RATE_SERVICE,
                    uuids.CSC_SERVICE]


@dataclass
class Discovered:
    address: str
    name: str
    rssi: int
    drivers: list[str]  # best first

    @property
    def roles(self) -> set[str]:
        return {r.value for d in self.drivers for r in DRIVERS[d].ROLES}


async def scan(timeout: float = 6.0) -> list[Discovered]:
    found = await BleakScanner.discover(timeout=timeout, return_adv=True,
                                        service_uuids=FITNESS_SERVICES)
    out: list[Discovered] = []
    for address, (dev, adv) in found.items():
        drivers = drivers_for_services(adv.service_uuids or [])
        if not drivers:
            continue
        out.append(Discovered(address=address, name=adv.local_name or dev.name or address,
                              rssi=adv.rssi, drivers=[d.DRIVER for d in drivers]))
    return sorted(out, key=lambda d: -d.rssi)


def make_device(driver: str, address: str, name: str = "") -> Device:
    return DRIVERS[driver](address, name)
