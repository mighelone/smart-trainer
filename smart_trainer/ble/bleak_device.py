"""Common BLE connection handling on top of bleak."""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient

from smart_trainer.ble.device import Device
from smart_trainer.core.state import ConnState

log = logging.getLogger(__name__)


class BleakDevice(Device):
    CONNECT_TIMEOUT = 20.0

    def __init__(self, address: str, name: str = "") -> None:
        super().__init__(address, name)
        self.client: BleakClient | None = None

    async def run_connected(self) -> None:
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
        """Subscribe to notifications / initialise the device."""
        raise NotImplementedError
