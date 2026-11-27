"""OpenBikeControl: protocol messages, the network device as a shifter, and discovery."""

import asyncio

import pytest

from smart_trainer.ble.device import DriverId
from smart_trainer.config import Settings
from smart_trainer.core.devices import DeviceHub
from smart_trainer.core.state import ConnState, Role
from smart_trainer.obc.device import APP_ID, NO_CONTROLLER, OpenBikeControl
from smart_trainer.obc.discovery import to_discovered
from smart_trainer.obc.protocol import ButtonState, DeviceStatus, encode_app_info, parse

SERVICE = "BikeControl._openbikecontrol._tcp.local."
UP_PRESSED, UP_RELEASED = b"\x01\x01\x01", b"\x01\x01\x00"
DOWN_PRESSED, DOWN_RELEASED = b"\x01\x02\x01", b"\x01\x02\x00"


@pytest.mark.parametrize(
    ("chunk", "messages"),
    [
        (UP_PRESSED, [ButtonState({1: 1})]),
        (b"\x01\x01\x01\x02\x00", [ButtonState({1: 1, 2: 0})]),
        (b"\x01\x01\x01\x02", [ButtonState({1: 1})]),  # half a pair at the end
        (b"\x02\x55\x01", [DeviceStatus(85, True)]),
        (b"\x02\xff\x00", [DeviceStatus(None, False)]),
        (b"\x02\x55\x01\x01\x02\x01", [DeviceStatus(85, True), ButtonState({2: 1})]),
        (b"\x03\x01\x00\x00", []),  # not a message a device sends
        (b"", []),
    ],
)
def test_parse(chunk, messages):
    assert parse(chunk) == messages


def test_app_info_matches_the_spec_example():
    expected = bytes([0x04, 0x01, 0x05, *b"zwift", 0x06, *b"1.52.0", 0x04, 0x01, 0x02, 0x10, 0x14])
    assert encode_app_info("zwift", "1.52.0", [0x01, 0x02, 0x10, 0x14]) == expected


class FakeWriter:
    """Records what the device sends, like the write end of a TCP stream."""

    def __init__(self) -> None:
        self.sent = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.sent += data

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeLink:
    """Opens streams that the test writes into, one per connection."""

    def __init__(self) -> None:
        self.readers: list[asyncio.StreamReader] = []
        self.writers: list[FakeWriter] = []
        self.opened: list[str] = []

    async def open(self, service_name: str) -> tuple[asyncio.StreamReader, FakeWriter]:
        self.opened.append(service_name)
        self.readers.append(reader := asyncio.StreamReader())
        self.writers.append(writer := FakeWriter())
        return reader, writer


@pytest.fixture
def link() -> FakeLink:
    return FakeLink()


@pytest.fixture
def device(link) -> OpenBikeControl:
    return OpenBikeControl(SERVICE, "BikeControl", link=link)


async def _connect(device: OpenBikeControl, link: FakeLink) -> asyncio.Task:
    """Run one connection in the background until it is up."""
    task = asyncio.create_task(device.run_connected())
    await asyncio.sleep(0.01)  # let the device open the stream and say hello
    assert device.state is ConnState.CONNECTED
    return task


async def _receive(link: FakeLink, *chunks: bytes) -> None:
    """Send chunks on the last connection, each as its own read."""
    for chunk in chunks:
        link.readers[-1].feed_data(chunk)
        await asyncio.sleep(0.01)


async def test_each_connection_starts_with_app_info_and_ends_when_the_device_hangs_up(device, link):
    for _ in range(2):
        task = await _connect(device, link)
        link.readers[-1].feed_eof()
        await asyncio.wait_for(task, 1)
    assert link.opened == [SERVICE, SERVICE]
    for writer in link.writers:
        assert writer.sent.startswith(bytes([0x04, 0x01, len(APP_ID), *APP_ID.encode()]))
        assert writer.sent.endswith(b"\x02\x01\x02")  # handles two buttons: shift up and down
        assert writer.closed


@pytest.fixture
async def shifts(device, link):
    """The shifts the hub forwards from the device, paired and connected as shifter."""
    forwarded: list[int] = []
    hub = DeviceHub(Settings(auto_upload=False), lambda reading: None, on_shift=forwarded.append)
    hub.add(device, [Role.SHIFTER])
    await asyncio.sleep(0.01)  # let it connect
    assert device.state is ConnState.CONNECTED
    yield forwarded
    await hub.stop_all()


async def test_presses_shift_once_each(device, link, shifts):
    await _receive(link, UP_PRESSED, UP_RELEASED, DOWN_PRESSED, DOWN_RELEASED)
    await _receive(link, UP_PRESSED, UP_PRESSED, UP_RELEASED)  # still held: one shift
    assert shifts == [1, -1, 1]


async def test_buttons_the_app_doesnt_handle_are_ignored(device, link, shifts):
    await _receive(link, b"\x01\x14\x01", b"\x01\x30\x01\x01\x01")
    assert shifts == [1]


async def test_a_new_connection_forgets_held_buttons(device, link):
    shifted: list[int] = []
    device.subscribe_shifts(shifted.append)
    first = await _connect(device, link)
    await _receive(link, UP_PRESSED)  # the release is lost with the connection
    link.readers[-1].feed_eof()
    await asyncio.wait_for(first, 1)
    second = await _connect(device, link)
    await _receive(link, UP_PRESSED)
    assert shifted == [1, 1]
    second.cancel()


async def test_problem_follows_the_controller_status(device, link, shifts):
    assert device.problem is None
    await _receive(link, b"\x02\x55\x00")
    assert device.problem == NO_CONTROLLER
    await _receive(link, b"\x02\x55\x01")
    assert device.problem is None


@pytest.mark.parametrize(
    ("properties", "name"),
    [
        ({b"name": b"BikeControl iPhone", b"version": b"1"}, "BikeControl iPhone"),
        ({b"version": b"1"}, "BikeControl"),
        ({b"name": None}, "BikeControl"),
    ],
)
def test_found_service_is_a_network_shifter(properties, name):
    found = to_discovered(SERVICE, properties)
    assert (found.address, found.name, found.rssi) == (SERVICE, name, None)
    assert found.drivers == [DriverId.OPEN_BIKE_CONTROL]
    assert found.roles == {Role.SHIFTER.value}
