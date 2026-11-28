"""FTMS trainer driver against a fake BLE client: notifications and taking control."""

import struct
from collections.abc import Callable

import pytest

from smart_trainer.ble import uuids
from smart_trainer.ble.ftms import FtmsTrainer
from smart_trainer.ble.parsers.ftms import FtmsOp, FtmsStatus

SUCCESS = 0x01


class FakeClient:
    """BLE client double: records subscriptions and writes, answers control point commands."""

    def __init__(self, status_supported: bool = True) -> None:
        self.handlers: dict[str, Callable[[object, bytearray], None]] = {}
        self.writes: list[bytes] = []
        self.status_supported = status_supported
        self.is_connected = True

    async def start_notify(self, uuid: str, handler) -> None:
        if uuid == uuids.FTMS_STATUS and not self.status_supported:
            raise RuntimeError("characteristic not found")
        self.handlers[uuid] = handler

    async def write_gatt_char(self, uuid: str, payload: bytes, response: bool) -> None:
        self.writes.append(payload)
        self.notify(uuids.FTMS_CONTROL_POINT, bytes([FtmsOp.RESPONSE, payload[0], SUCCESS]))

    def notify(self, uuid: str, data: bytes) -> None:
        self.handlers[uuid](None, bytearray(data))


async def _connect(client: FakeClient) -> FtmsTrainer:
    trainer = FtmsTrainer("AA:BB", "Hub")
    trainer.client = client  # type: ignore[assignment]  (what run_connected does)
    await trainer.on_connected(client)  # type: ignore[arg-type]
    return trainer


@pytest.mark.parametrize("status_supported", [True, False])
async def test_connecting_subscribes_and_takes_control(status_supported):
    client = FakeClient(status_supported)
    trainer = await _connect(client)
    assert {uuids.FTMS_CONTROL_POINT, uuids.INDOOR_BIKE_DATA} <= client.handlers.keys()
    assert (uuids.FTMS_STATUS in client.handlers) == status_supported
    assert [w[0] for w in client.writes] == [FtmsOp.REQUEST_CONTROL, FtmsOp.START_RESUME]
    assert trainer.has_control


async def test_bike_data_is_emitted_as_a_reading():
    client = FakeClient()
    trainer = await _connect(client)
    readings = []
    trainer.subscribe(readings.append)
    flags = 0x0044  # instantaneous cadence and power present
    client.notify(uuids.INDOOR_BIKE_DATA, struct.pack("<HHHh", flags, 3000, 180, 210))
    [reading] = readings
    assert (reading.power, reading.cadence, reading.speed_kmh) == (210, 90, 30.0)
    assert trainer.speed_kmh == 30.0


async def test_losing_control_is_noticed():
    client = FakeClient()
    trainer = await _connect(client)
    client.notify(uuids.FTMS_STATUS, bytes([FtmsStatus.CONTROL_LOST]))
    assert not trainer.has_control


async def test_no_command_is_written_once_the_link_is_down():
    client = FakeClient()
    trainer = await _connect(client)
    client.is_connected = False  # the trainer dropped; bleak hasn't told the app yet
    await trainer.set_slope(1.0)
    assert [w[0] for w in client.writes] == [FtmsOp.REQUEST_CONTROL, FtmsOp.START_RESUME]
