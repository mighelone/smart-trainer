"""Drivers for the Zwift Click v2: its buttons make the trainer harder or easier."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from bleak import BleakClient

from smart_trainer.ble import uuids
from smart_trainer.ble.bleak_device import BleakDevice
from smart_trainer.ble.device import DriverId, Shifter, register
from smart_trainer.ble.parsers.zwift import (
    IGNORED_MESSAGES,
    Button,
    is_unlock_request,
    parse_buttons,
)
from smart_trainer.core.state import Role

log = logging.getLogger(__name__)

# Handshake: written to the sync RX characteristic; the controller answers with the same bytes
# on sync TX and then sends unencrypted messages. One that Zwift has locked answers too, but
# then sends an unlock request (see parsers/zwift.py) and, after about a minute, no buttons.
RIDE_ON = b"RideOn"
LOCKED = "Zwift Click is locked: unlock it in the Zwift app, or pair the right controller"
# The right controller drops the link 60 s after connecting or after the last press, then
# sleeps until a press wakes it (and that press is lost). Reconnecting a little earlier keeps it
# awake; keepalive writes and reads don't reset its timer (both tried on a Click v2).
RENEW_AFTER_S = 50.0
RENEW_POLL_S = 1.0  # how often to check whether the renewal is due, in seconds


@dataclass
class KeepAwake:
    """Tells when to renew a connection: ``after_s`` since it was made or last used.

    Attributes:
        after_s (float): Seconds of quiet after which to renew.
        now (Callable[[], float]): Monotonic clock (injected for tests).
        last (float): When the connection was last made or used.
    """

    after_s: float = RENEW_AFTER_S
    now: Callable[[], float] = time.monotonic
    last: float = field(init=False, default=0.0)

    def touch(self) -> None:
        """Note that the connection was just made or used."""
        self.last = self.now()

    def due(self) -> bool:
        """Whether the connection should be renewed now.

        Returns:
            bool: ``True`` once ``after_s`` passed since the last :meth:`touch`.
        """
        return self.now() - self.last >= self.after_s


@register
class ZwiftClick(BleakDevice, Shifter):
    """A Zwift Click v2 controller that relays both sides' buttons (type ``0x0B``).

    Zwift locks it unless the Zwift app unlocked it in the last ~24 h: :attr:`problem` then
    says so. :class:`ZwiftClickRight` is the controller that works without an unlock.
    """

    DRIVER = DriverId.ZWIFT_CLICK
    SERVICE_UUID = uuids.ZWIFT_SERVICE
    ROLES = frozenset({Role.SHIFTER})

    def __init__(self, address: str, name: str = "") -> None:
        """Initialize the controller.

        Args:
            address (str): BLE address (or platform UUID on macOS) of the controller.
            name (str): Human-readable name; defaults to the address.
        """
        BleakDevice.__init__(self, address, name)
        Shifter.__init__(self)
        self._unknown: set[int] = set()  # message types already warned about
        self._locked = False  # an unlock request came, and no button message since

    @property
    def problem(self) -> str | None:
        """Why the buttons don't work: the controller is locked by Zwift, or ``None``.

        Returns:
            str | None: The message for the rider, or ``None``.
        """
        return LOCKED if self._locked else None

    async def on_connected(self, client: BleakClient) -> None:
        """Subscribe to the controller's messages and send the handshake.

        Args:
            client (BleakClient): The connected bleak client.
        """
        self.release_all()
        self._locked = False
        await client.start_notify(uuids.ZWIFT_ASYNC, lambda _c, data: self.handle(bytes(data)))
        await client.start_notify(
            uuids.ZWIFT_SYNC_TX, lambda _c, data: self.handle_reply(bytes(data))
        )
        await client.write_gatt_char(uuids.ZWIFT_SYNC_RX, RIDE_ON, response=False)

    def handle(self, data: bytes) -> None:
        """Handle a message from the notify characteristic: shift on button presses.

        Messages that aren't button states are ignored; an unknown kind is logged once, as
        a newer firmware may have changed the protocol.

        Args:
            data (bytes): Raw notification payload.
        """
        pressed = parse_buttons(data)
        if pressed is not None:
            self._locked = False  # it sends buttons, whatever it asked before
            self.update_buttons(pressed)
            return
        if is_unlock_request(data):
            if not self._locked:
                log.warning("%s: locked by Zwift (unlock request received)", self.name)
            self._locked = True
            return
        kind = data[0] if data else -1
        if kind not in IGNORED_MESSAGES and kind not in self._unknown:
            self._unknown.add(kind)
            log.warning("%s: unknown Zwift message %s", self.name, data.hex(" "))

    def handle_reply(self, data: bytes) -> None:
        """Check the handshake reply: anything but ``RideOn`` means the buttons won't work.

        Args:
            data (bytes): Raw indication payload.
        """
        if data.startswith(RIDE_ON):
            log.info("%s: handshake accepted", self.name)
        else:
            log.warning(
                "%s: handshake refused (%s): the controller may be locked by Zwift",
                self.name,
                data[:8].hex(" "),
            )


@register
class ZwiftClickRight(ZwiftClick):
    """The right controller of a Zwift Click v2 pair (type ``0x0A``): never locked by Zwift.

    It only reports its own buttons (+ is harder, B easier) and drops the link 60 s after the
    last press, so the connection is renewed before that (see :data:`RENEW_AFTER_S`).

    Attributes:
        keep_awake (KeepAwake): When to renew the connection.
    """

    DRIVER = DriverId.ZWIFT_CLICK_RIGHT
    SERVICE_UUID = ""  # matched by its Zwift device type, not by the service (see the scanner)

    def __init__(self, address: str, name: str = "", keep_awake: KeepAwake | None = None) -> None:
        """Initialize the controller.

        Args:
            address (str): BLE address (or platform UUID on macOS) of the controller.
            name (str): Human-readable name; defaults to the address.
            keep_awake (KeepAwake | None): When to renew the connection; defaults to
                :data:`RENEW_AFTER_S` of quiet.
        """
        super().__init__(address, name)
        self.keep_awake = keep_awake or KeepAwake()

    async def on_connected(self, client: BleakClient) -> None:
        """Subscribe and send the handshake, and start the quiet time.

        Args:
            client (BleakClient): The connected bleak client.
        """
        self.keep_awake.touch()
        await super().on_connected(client)

    def update_buttons(self, pressed: frozenset[Button]) -> None:
        """Shift for new presses; a press also restarts the controller's own 60 s timer.

        Args:
            pressed (frozenset[Button]): The buttons pressed now.
        """
        if pressed:
            self.keep_awake.touch()
        super().update_buttons(pressed)

    async def renewal_due(self) -> None:
        """Return once the connection has been quiet for the keep-awake time."""
        while not self.keep_awake.due():
            await asyncio.sleep(RENEW_POLL_S)
