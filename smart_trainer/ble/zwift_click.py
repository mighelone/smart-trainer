"""Driver for the Zwift Click v2: its + and − buttons shift the virtual gears."""

from __future__ import annotations

import logging

from bleak import BleakClient

from smart_trainer.ble import uuids
from smart_trainer.ble.bleak_device import BleakDevice
from smart_trainer.ble.device import DriverId, Shifter, register
from smart_trainer.ble.parsers.zwift import IGNORED_MESSAGES, parse_buttons
from smart_trainer.core.state import Role

log = logging.getLogger(__name__)

# Handshake: written to the sync RX characteristic; an unlocked controller answers with the
# same bytes on sync TX and then sends unencrypted messages. A controller Zwift has locked
# answers with a challenge starting with 0xFF instead.
RIDE_ON = b"RideOn"


@register
class ZwiftClick(BleakDevice, Shifter):
    """A Zwift Click v2 controller.

    Of a Click v2 pair, pair the controller that reports both buttons (see
    :data:`~smart_trainer.ble.parsers.zwift.CLICK_V2_BOTH`); the other reports only its own.
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

    async def on_connected(self, client: BleakClient) -> None:
        """Subscribe to the controller's messages and send the handshake.

        Args:
            client (BleakClient): The connected bleak client.
        """
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
            self.update_buttons(pressed)
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
