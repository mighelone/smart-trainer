"""Common BLE connection handling on top of bleak."""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient

from smart_trainer.ble.device import Device
from smart_trainer.core.state import ConnState

log = logging.getLogger(__name__)


class BleakDevice(Device):
    """Base class for devices connected through a bleak ``BleakClient``.

    Attributes:
        CONNECT_TIMEOUT (float): Connection timeout in seconds.
        client (BleakClient | None): Active client while connected, else ``None``.
    """

    CONNECT_TIMEOUT = 20.0

    def __init__(self, address: str, name: str = "") -> None:
        """Initialize the device.

        Args:
            address (str): BLE address (or platform UUID on macOS) of the device.
            name (str): Human-readable device name; defaults to the address.
        """
        super().__init__(address, name)
        self.client: BleakClient | None = None

    async def run_connected(self) -> None:
        """Connect to the device and block until stopped or disconnected.

        Calls :meth:`on_connected` once the link is up, then sets the state to
        ``CONNECTED`` and waits for either a stop request or a lost connection.
        """
        lost = asyncio.Event()
        client = BleakClient(
            self.address, disconnected_callback=lambda _c: lost.set(), timeout=self.CONNECT_TIMEOUT
        )
        log.info("%s: connecting", self.name)
        async with client:
            self.client = client
            try:
                await self.on_connected(client)
                self.state = ConnState.CONNECTED
                log.info("%s: connected", self.name)
                stop_wait = asyncio.create_task(self._stop.wait())
                lost_wait = asyncio.create_task(lost.wait())
                await asyncio.wait({stop_wait, lost_wait}, return_when=asyncio.FIRST_COMPLETED)
                stop_wait.cancel()
                lost_wait.cancel()
            finally:
                self.client = None
        log.info("%s: disconnected", self.name)

    async def on_connected(self, client: BleakClient) -> None:
        """Subscribe to notifications / initialise the device.

        Args:
            client (BleakClient): The connected bleak client.

        Raises:
            NotImplementedError: Must be implemented by subclasses.
        """
        raise NotImplementedError
