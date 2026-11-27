"""Parsers for the Zwift controller protocol (Zwift Click v2).

Zwift doesn't document the protocol. What is here comes from community reverse engineering
(https://www.makinolo.com/blog/2024/07/26/zwift-ride-protocol/) and was checked against
captures of a Click v2 pair.

Messages on the notify characteristic start with a type byte followed by a protobuf
message. The button state message looks like::

    23 08 ff df ff ff 0f
    |  |  '------------ varint: 32-bit button bitmap, a bit at 0 means "pressed"
    |  '--------------- protobuf tag: field 1, wire type 0 (varint)
    '------------------ message type 0x23: button state

``ff ff ff ff 0f`` is ``0xFFFFFFFF``: nothing pressed. While a button is held, the
controller repeats the message about every 0.1 s.

A Click v2 pair is two controllers. The right one (device type ``0x0A``) has five buttons and
reports only its own: Y (bit 6, top), Z (bit 7), A (bit 4), B (bit 5) and + (bit 12, bottom).
The other one (``0x0B``) also relays the right one's buttons, and its own − is bit 8. Zwift
locks the relaying controller unless the Zwift app unlocked it in the last ~24 h: it then
sends an unlock request (``ff 03 …``) a few seconds after connecting, and no button messages
after about a minute. The right controller isn't locked.
"""

from __future__ import annotations

from enum import StrEnum

ZWIFT_COMPANY_ID = 0x094A  # Bluetooth company ID in the manufacturer data of Zwift devices
# Device type, the first byte of the manufacturer data
CLICK_V2_BOTH = 0x0B  # relays both controllers' buttons; locked by Zwift without an unlock
CLICK_V2_SIDE = 0x0A  # the right controller: only its own buttons, never locked

BUTTON_STATE = 0x23  # message type of the button bitmap
BITMAP_TAG = 0x08  # protobuf tag of field 1 (varint) holding the bitmap
# Other message types the Click sends, all ignored: 0x15 and 0x19 idle and battery (``19 10 64``
# = 100 %), 0x2A device information after connecting, 0xFF serial number and other status.
IGNORED_MESSAGES = frozenset({0x15, 0x19, 0x2A, 0xFF})
# Start of Zwift's unlock request, sent by a locked controller (it carries a public key for
# Zwift's encrypted handshake, which only the Zwift app can answer)
UNLOCK_REQUEST = b"\xff\x03"


class Button(StrEnum):
    """A Click button that shifts."""

    PLUS = "plus"
    MINUS = "minus"
    B = "b"


# Bit of each button that shifts, from the captures. The right controller's A, Y and Z (bits
# 4, 6, 7) are not used yet.
BUTTON_BIT = {Button.PLUS: 12, Button.MINUS: 8, Button.B: 5}


def _varint(data: bytes, offset: int) -> int | None:
    """Decode a protobuf varint: 7 bits per byte, least significant first, high bit = more.

    Args:
        data (bytes): The message.
        offset (int): Index of the varint's first byte.

    Returns:
        int | None: The value, or ``None`` if the message ends before the varint does.
    """
    value = 0
    for index, byte in enumerate(data[offset:]):
        value |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return value
    return None


def parse_buttons(data: bytes) -> frozenset[Button] | None:
    """Decode the pressed buttons from a button state message.

    Args:
        data (bytes): Raw notification payload.

    Returns:
        frozenset[Button] | None: The buttons pressed (empty when none), or ``None`` if the
        message isn't a well-formed button state message.
    """
    if len(data) < 3 or data[0] != BUTTON_STATE or data[1] != BITMAP_TAG:
        return None
    bitmap = _varint(data, 2)
    if bitmap is None:
        return None
    return frozenset(button for button, bit in BUTTON_BIT.items() if not bitmap >> bit & 1)


def is_unlock_request(data: bytes) -> bool:
    """Whether a message is the unlock request of a controller Zwift has locked.

    Args:
        data (bytes): Raw notification payload.

    Returns:
        bool: ``True`` for an unlock request.
    """
    return data.startswith(UNLOCK_REQUEST)


def zwift_device_type(manufacturer_data: dict[int, bytes]) -> int | None:
    """The Zwift device type from a BLE advertisement.

    Args:
        manufacturer_data (dict[int, bytes]): Manufacturer data by company ID.

    Returns:
        int | None: The device type byte (e.g. ``CLICK_V2_BOTH``), or ``None`` if the
        advertisement doesn't come from a Zwift device.
    """
    data = manufacturer_data.get(ZWIFT_COMPANY_ID)
    return data[0] if data else None
