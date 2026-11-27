"""BikeControl (or any OpenBikeControl device) on the local network as a shifter."""

from __future__ import annotations

import asyncio
import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

from smart_trainer.ble.device import Device, DriverId, Shifter, register
from smart_trainer.core.state import ConnState, Role
from smart_trainer.obc.protocol import (
    SERVICE_TYPE,
    ButtonState,
    DeviceStatus,
    ObcButton,
    encode_app_info,
    parse,
)

log = logging.getLogger(__name__)

APP_ID = "smart-trainer"  # how the app introduces itself in the app info message
RESOLVE_TIMEOUT_S = 3.0  # how long to wait for the device's address on the network
CONNECT_TIMEOUT_S = 5.0  # TCP connection timeout, in seconds
READ_SIZE = 256  # bytes per read; a message is at most a few dozen
NO_CONTROLLER = "BikeControl has no controller connected"


def _app_version() -> str:
    """The installed version of the app, sent to the device.

    Returns:
        str: The version, or ``"0"`` when running from a tree that isn't installed.
    """
    try:
        return version("smart-trainer")
    except PackageNotFoundError:
        return "0"


class Writer(Protocol):
    """The part of :class:`asyncio.StreamWriter` the device uses."""

    def write(self, data: bytes) -> None:
        """Queue bytes to send.

        Args:
            data (bytes): The bytes.
        """
        ...

    async def drain(self) -> None:
        """Wait until the queued bytes can be sent."""
        ...

    def close(self) -> None:
        """Close the connection."""
        ...


class Link(Protocol):
    """Opens the TCP stream to a device advertised on the network."""

    async def open(self, service_name: str) -> tuple[asyncio.StreamReader, Writer]:
        """Find the device and connect to it.

        Args:
            service_name (str): Its mDNS service name.

        Returns:
            tuple[asyncio.StreamReader, Writer]: The stream's two ends.

        Raises:
            OSError: If the device isn't on the network or refuses the connection.
        """
        ...


class ZeroconfLink:
    """Resolves the device with mDNS on every connection: its address and port can change."""

    async def open(self, service_name: str) -> tuple[asyncio.StreamReader, Writer]:
        """Resolve the service and open a TCP connection to it.

        Args:
            service_name (str): Its mDNS service name.

        Returns:
            tuple[asyncio.StreamReader, Writer]: The stream's two ends.

        Raises:
            ConnectionError: If the service doesn't answer on the network.
        """
        host, port = await self._resolve(service_name)
        log.debug("%s: connecting to %s:%d", service_name, host, port)
        return await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT_S)

    @staticmethod
    async def _resolve(service_name: str) -> tuple[str, int]:
        """Find the address and port of a service.

        Args:
            service_name (str): Its mDNS service name.

        Returns:
            tuple[str, int]: Host and port; IPv4 first, as it works on every network.

        Raises:
            ConnectionError: If the service doesn't answer.
        """
        info = AsyncServiceInfo(SERVICE_TYPE, service_name)
        async with AsyncZeroconf() as azc:
            found = await info.async_request(azc.zeroconf, RESOLVE_TIMEOUT_S * 1000)
        hosts = info.parsed_addresses(IPVersion.V4Only) or info.parsed_addresses()
        if not found or not hosts or info.port is None:
            raise ConnectionError(f"{service_name} not found on the network")
        return hosts[0], info.port


@register
class OpenBikeControl(Device, Shifter):
    """A device that relays controller buttons with OpenBikeControl over TCP, e.g. BikeControl.

    Its address is its mDNS service name. Shift Up makes the trainer harder, Shift Down
    easier.
    """

    DRIVER = DriverId.OPEN_BIKE_CONTROL
    ROLES = frozenset({Role.SHIFTER})
    SHIFT_STEPS = {ObcButton.SHIFT_UP: 1, ObcButton.SHIFT_DOWN: -1}

    def __init__(self, address: str, name: str = "", link: Link | None = None) -> None:
        """Initialize the device.

        Args:
            address (str): mDNS service name of the device.
            name (str): Human-readable name; defaults to the address.
            link (Link | None): Opens the connection; defaults to resolving with mDNS.
        """
        Device.__init__(self, address, name)
        Shifter.__init__(self)
        self.link = link or ZeroconfLink()
        self._held: set[int] = set()  # button ids reported pressed and not released yet
        self._controller = True  # whether the device says a controller is connected

    @property
    def problem(self) -> str | None:
        """Why the buttons don't work: the device has no controller connected, or ``None``.

        Returns:
            str | None: The message for the rider, or ``None``.
        """
        return None if self._controller else NO_CONTROLLER

    async def run_connected(self) -> None:
        """Connect, say which buttons the app handles, and read until the device hangs up."""
        reader, writer = await self.link.open(self.address)
        try:
            self._held.clear()
            self.release_all()
            self._controller = True
            writer.write(encode_app_info(APP_ID, _app_version(), list(ObcButton)))
            await writer.drain()
            self.state = ConnState.CONNECTED
            log.info("%s: connected", self.name)
            while chunk := await reader.read(READ_SIZE):
                self.handle(chunk)
            log.info("%s: closed by the device", self.name)
        finally:
            writer.close()

    def handle(self, chunk: bytes) -> None:
        """Handle bytes from the device: shift on presses, note its status.

        Args:
            chunk (bytes): Bytes from one read.
        """
        log.debug("%s: received %s", self.name, chunk.hex(" "))
        for message in parse(chunk):
            if isinstance(message, ButtonState):
                for button, state in message.states.items():
                    if state:
                        self._held.add(button)
                    else:
                        self._held.discard(button)
                self.update_buttons(frozenset(self._held))
            elif isinstance(message, DeviceStatus):
                if message.connected != self._controller:
                    log.info("%s: controller connected: %s", self.name, message.connected)
                self._controller = message.connected
