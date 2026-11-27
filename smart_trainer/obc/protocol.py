"""Messages of the OpenBikeControl protocol, which BikeControl uses to relay controller buttons.

Spec: https://github.com/OpenBikeControl/openbikecontrol-protocol (PROTOCOL.md, MDNS.md).
Every message starts with a type byte; the same bytes travel over BLE and TCP::

    01 01 01 02 00     button state: (button id, state) pairs; Shift Up pressed, Down released
    02 55 01           device status: battery 85 %, controller connected
    04 01 05 ...       app info, sent by the app: which buttons it handles

Over TCP the messages carry no length. Device status has a fixed length, button state runs
to the end of what was received: like the spec's example app, this relies on the device
writing each message on its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import IntEnum

log = logging.getLogger(__name__)

SERVICE_TYPE = "_openbikecontrol._tcp.local."  # mDNS service the device advertises (MDNS.md)

BUTTON_STATE = 0x01  # device → app: (button id, state) pairs
DEVICE_STATUS = 0x02  # device → app, TCP only: battery, connected
APP_INFO = 0x04  # app → device: app id, version, buttons it handles
APP_INFO_VERSION = 0x01  # version of the app info layout
STATUS_LENGTH = 3  # type, battery, connected
NO_BATTERY = 0xFF  # battery byte of a device without one
MAX_TEXT = 32  # longest app id or version, in UTF-8 bytes


class ObcButton(IntEnum):
    """Button ids the app handles (PROTOCOL.md, "Gear shifting")."""

    SHIFT_UP = 0x01
    SHIFT_DOWN = 0x02


@dataclass(frozen=True)
class ButtonState:
    """A button state message: the state of the buttons that changed.

    Attributes:
        states (dict[int, int]): State by button id: ``0`` released, ``1`` pressed, more for
            analog buttons.
    """

    states: dict[int, int]


@dataclass(frozen=True)
class DeviceStatus:
    """A device status message.

    Attributes:
        battery (int | None): Battery level in percent, ``None`` if the device has none.
        connected (bool): Whether the device has a controller connected and is ready.
    """

    battery: int | None
    connected: bool


Message = ButtonState | DeviceStatus


def encode_app_info(app_id: str, version: str, buttons: list[int]) -> bytes:
    """Encode the app info message: who the app is and which buttons it handles.

    Args:
        app_id (str): Lowercase app id, at most :data:`MAX_TEXT` bytes.
        version (str): App version, at most :data:`MAX_TEXT` bytes.
        buttons (list[int]): Button ids the app handles; empty means all of them.

    Returns:
        bytes: The message.
    """
    app = app_id.encode()[:MAX_TEXT]
    ver = version.encode()[:MAX_TEXT]
    return bytes(
        [APP_INFO, APP_INFO_VERSION, len(app), *app, len(ver), *ver, len(buttons), *buttons]
    )


def parse(chunk: bytes) -> list[Message]:
    """Decode the messages in what was received in one read.

    Unknown message types end the decoding, as their length is unknown: they are logged.

    Args:
        chunk (bytes): Bytes from one read of the stream.

    Returns:
        list[Message]: The messages, in order.
    """
    messages: list[Message] = []
    rest = chunk
    while rest:
        if rest[0] == DEVICE_STATUS and len(rest) >= STATUS_LENGTH:
            battery = None if rest[1] == NO_BATTERY else rest[1]
            messages.append(DeviceStatus(battery, rest[2] == 1))
            rest = rest[STATUS_LENGTH:]
        elif rest[0] == BUTTON_STATE:
            pairs = rest[1:]
            # a trailing odd byte is half a pair: ignored
            messages.append(ButtonState(dict(zip(pairs[::2], pairs[1::2], strict=False))))
            rest = b""
        else:
            log.warning("unknown OpenBikeControl message %s", rest.hex(" "))
            rest = b""
    return messages
