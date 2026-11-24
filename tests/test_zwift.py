import logging

import pytest

from smart_trainer.ble.device import DriverId
from smart_trainer.ble.parsers.zwift import (
    CLICK_V2_BOTH,
    ZWIFT_COMPANY_ID,
    Button,
    parse_buttons,
    zwift_device_type,
)
from smart_trainer.ble.scanner import discovered
from smart_trainer.ble.uuids import HEART_RATE_SERVICE, ZWIFT_SERVICE
from smart_trainer.ble.zwift_click import RIDE_ON, ZwiftClick

# Messages captured from a Zwift Click v2 pair
IDLE = bytes.fromhex("23 08 ff ff ff ff 0f")
PLUS = bytes.fromhex("23 08 ff df ff ff 0f")
MINUS = bytes.fromhex("23 08 ff fd ff ff 0f")
BOTH = bytes.fromhex("23 08 ff dd ff ff 0f")
OTHER_BIT = bytes.fromhex("23 08 ef ff ff ff 0f")  # an unknown button: not a shift
BATTERY = bytes.fromhex("19 10 64")
DEVICE_INFO = bytes.fromhex("2a 08 03 12 12 12 10 08 0c 10 00 18 24 22 05 00 00 00 d3 1f 28 fb 01")
SERIAL = bytes.fromhex(
    "ff 05 00 ea 05 18 0a 0c 44 34 30 36 30 46 41 44 36 39 45 36 10 00 18 0f 20 00 28 01 30 00"
)


@pytest.mark.parametrize(
    ("message", "pressed"),
    [
        (IDLE, set()),
        (PLUS, {Button.PLUS}),
        (MINUS, {Button.MINUS}),
        (BOTH, {Button.PLUS, Button.MINUS}),
        (OTHER_BIT, set()),
        (BATTERY, None),
        (DEVICE_INFO, None),
        (SERIAL, None),
        (bytes.fromhex("23 08 ff df"), None),  # cut off in the middle of the varint
        (bytes.fromhex("23 10 ff"), None),  # not field 1
        (b"", None),
    ],
)
def test_parse_buttons(message, pressed):
    result = parse_buttons(message)
    assert result == (None if pressed is None else frozenset(pressed))


@pytest.mark.parametrize(
    ("manufacturer_data", "device_type"),
    [
        ({ZWIFT_COMPANY_ID: bytes.fromhex("0b 69 e6")}, CLICK_V2_BOTH),
        ({ZWIFT_COMPANY_ID: bytes.fromhex("0a 2f da")}, 0x0A),
        ({0x0059: b"\x01\x02"}, None),
        ({}, None),
    ],
)
def test_zwift_device_type(manufacturer_data, device_type):
    assert zwift_device_type(manufacturer_data) == device_type


def test_scan_matches_a_click_to_its_driver():
    found = discovered(
        "CAE4", "Zwift Click", -48, [ZWIFT_SERVICE], {ZWIFT_COMPANY_ID: bytes.fromhex("0b 69 e6")}
    )
    assert found is not None
    assert found.drivers == [DriverId.ZWIFT_CLICK] and found.roles == {"shifter"}
    assert found.zwift_type == CLICK_V2_BOTH


def test_scan_ignores_unknown_devices_and_types_others():
    assert discovered("A", "Speaker", -60, ["0000110b-0000-1000-8000-00805f9b34fb"], {}) is None
    strap = discovered("B", "HRM", -60, [HEART_RATE_SERVICE], {})
    assert strap is not None and strap.zwift_type is None


@pytest.fixture
def click() -> tuple[ZwiftClick, list[int]]:
    """A Click driver (not connected) and the shifts it reports."""
    device = ZwiftClick("CAE4", "Zwift Click")
    shifts: list[int] = []
    device.subscribe_shifts(shifts.append)
    return device, shifts


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        ([IDLE, PLUS, IDLE], [1]),
        ([IDLE, MINUS, IDLE, MINUS, IDLE], [-1, -1]),
        ([IDLE] + [PLUS] * 30 + [IDLE], [1]),  # held for 3 s: one shift
        ([PLUS, BOTH, PLUS, IDLE], [1, -1]),  # "-" pressed while "+" is held
        ([IDLE, BATTERY, PLUS, BATTERY, PLUS, IDLE], [1]),  # status messages in between
        ([IDLE, OTHER_BIT, IDLE], []),
    ],
)
def test_click_shifts_once_per_press(click, messages, expected):
    device, shifts = click
    for message in messages:
        device.handle(message)
    assert shifts == expected


def test_unknown_messages_are_logged_once(click, caplog):
    device, shifts = click
    with caplog.at_level(logging.WARNING):
        for message in [b"\x42\x01", b"\x42\x02", BATTERY, SERIAL]:
            device.handle(message)
    assert [r.getMessage() for r in caplog.records] == ["Zwift Click: unknown Zwift message 42 01"]
    assert shifts == []


@pytest.mark.parametrize(
    ("reply", "warned"), [(RIDE_ON, False), (bytes.fromhex("ff 03 00 0a 21"), True)]
)
def test_a_refused_handshake_is_logged(click, caplog, reply, warned):
    device, _ = click
    with caplog.at_level(logging.WARNING):
        device.handle_reply(reply)
    assert any("handshake refused" in r.getMessage() for r in caplog.records) is warned
