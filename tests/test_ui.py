"""UI tests with NiceGUI's simulated user (no browser): pages and the mini overlay."""

import asyncio
import sys

import pytest
from nicegui.testing import User

from smart_trainer.ble import fake
from smart_trainer.config import Settings
from smart_trainer.core.engine import Engine
from smart_trainer.core.ride import RideState
from smart_trainer.core.state import Role

pytestmark = pytest.mark.nicegui_main_file("tests/ui_main.py")


class FakeWindow:
    """Records the calls the app makes on the native window."""

    def __init__(self) -> None:
        self.on_top = False
        self.size = (1400, 900)
        self.position = (50, 60)

    async def get_size(self) -> tuple[int, int]:
        return self.size

    async def get_position(self) -> tuple[int, int]:
        return self.position

    def set_always_on_top(self, on_top: bool) -> None:
        self.on_top = on_top

    def resize(self, width: int, height: int) -> None:
        self.size = (width, height)

    def move(self, x: int, y: int) -> None:
        self.position = (x, y)


@pytest.fixture
def pages(user: User):
    """The pages module imported for this test's simulation (fresh in every test)."""
    return sys.modules["smart_trainer.ui.pages"]


@pytest.fixture
async def engine(pages, tmp_path):
    """Engine with simulated devices and no connections, installed in the pages module."""
    settings = Settings(ftp=200, auto_upload=False)
    settings._readonly = True
    e = Engine.from_settings(settings, sessions=tmp_path)
    e.devices.add(fake.FakeTrainer(), [Role.TRAINER, Role.SPEED])
    e.devices.add(fake.FakePowerMeter(), [Role.POWER, Role.CADENCE])
    e.devices.add(fake.FakeHeartRate(), [Role.HR])
    pages.S.engine, pages.S.settings, pages.S.fake = e, settings, True
    async with e:
        await asyncio.sleep(0.4)  # let the fake devices connect
        yield e


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("/", "Start"),
        ("/workouts", "Workouts"),
        ("/devices", "Scan"),
        ("/history", "History"),
        ("/settings", "Settings"),
    ],
)
async def test_pages_render(user: User, engine, path, text):
    await user.open(path)
    await user.should_see(text)


async def test_mini_toggle_in_browser(user: User, engine):
    await user.open("/")
    user.find(marker="mini").click()
    await user.should_see(marker="mini-expand", retries=20)
    await user.should_see("Free ride: press start", retries=20)
    user.find(marker="mini-expand").click()
    await user.should_see("Elapsed", retries=20)  # back on the full ride page


async def test_mini_start_and_pause(user: User, engine):
    await user.open("/mini")
    user.find(marker="mini-start").click()
    assert engine.ride.state == RideState.RIDING
    await user.should_see("Free ride ·", retries=20)
    user.find(marker="mini-pause").click()
    assert engine.ride.manual_pause
    await user.should_see("PAUSED", retries=20)


async def test_mini_native_window(user: User, engine, pages, monkeypatch):
    w = FakeWindow()
    monkeypatch.setattr(pages.app.native, "main_window", w)  # the app of this simulation
    await user.open("/")
    user.find(marker="mini").click()
    await user.should_see(marker="mini-expand", retries=20)
    assert w.on_top and w.size == pages.MINI_SIZE
    user.find(marker="mini-expand").click()
    await user.should_see("Elapsed", retries=20)
    assert not w.on_top and w.size == (1400, 900) and w.position == (50, 60)


async def test_start_in_mini(user: User, engine, pages):
    pages.S.start_mini = True
    await user.open("/")
    await user.should_see(marker="mini-expand", retries=20)
    assert not pages.S.start_mini
