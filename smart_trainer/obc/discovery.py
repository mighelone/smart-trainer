"""Find OpenBikeControl devices (e.g. BikeControl) advertised on the local network."""

from __future__ import annotations

import asyncio

from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from smart_trainer.ble.device import DriverId
from smart_trainer.ble.scanner import Discovered
from smart_trainer.obc.protocol import SERVICE_TYPE

RESOLVE_TIMEOUT_MS = 2000  # how long to wait for a found service's details, in milliseconds


def to_discovered(service_name: str, properties: dict[bytes, bytes | None]) -> Discovered:
    """Describe a found service as a device to pair.

    Args:
        service_name (str): Its mDNS service name, which identifies it.
        properties (dict[bytes, bytes | None]): Its TXT record.

    Returns:
        Discovered: The device, named by its TXT ``name`` or else by its service name.
    """
    txt_name = properties.get(b"name")
    name = txt_name.decode(errors="replace") if txt_name else service_name.split(".")[0]
    return Discovered(service_name, name, None, [DriverId.OPEN_BIKE_CONTROL])


async def browse(timeout: float = 6.0) -> list[Discovered]:
    """Look for OpenBikeControl services on the local network.

    Args:
        timeout (float): How long to look, in seconds.

    Returns:
        list[Discovered]: The devices found, by name.
    """
    names: set[str] = set()

    def on_change(
        zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        """Note a service that appeared.

        Args:
            zeroconf (Zeroconf): The browsing instance.
            service_type (str): The service type browsed.
            name (str): The service name.
            state_change (ServiceStateChange): What happened to it.
        """
        if state_change is ServiceStateChange.Added:
            names.add(name)

    async with AsyncZeroconf() as azc:
        browser = AsyncServiceBrowser(azc.zeroconf, SERVICE_TYPE, handlers=[on_change])
        await asyncio.sleep(timeout)
        await browser.async_cancel()
        found = []
        for name in sorted(names):
            info = AsyncServiceInfo(SERVICE_TYPE, name)
            await info.async_request(azc.zeroconf, RESOLVE_TIMEOUT_MS)
            found.append(to_discovered(name, info.properties))
    return sorted(found, key=lambda d: d.name)
