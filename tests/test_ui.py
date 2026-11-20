"""UI tests with NiceGUI's simulated user (no browser): pages and the mini overlay."""

import asyncio
import sys

import pytest
from nicegui import app, ui
from nicegui.testing import User

from smart_trainer.ble import fake
from smart_trainer.core.control import Mode
from smart_trainer.core.engine import Engine
from smart_trainer.core.ride import RideState
from smart_trainer.core.state import ConnState, Role
from smart_trainer.ui import connections
from smart_trainer.ui.context import AppContext, LiveRide
from smart_trainer.ui.pages.history import HistoryPage
from smart_trainer.ui.pages.live import ride_status
from smart_trainer.ui.window import MINI_SIZE

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
async def context(pages, tmp_path, settings):
    """App context with simulated devices and no connections; its pages are registered."""
    e = Engine.from_settings(settings, sessions=tmp_path)
    e.devices.add(fake.FakeTrainer(), [Role.TRAINER, Role.SPEED])
    e.devices.add(fake.FakePowerMeter(), [Role.POWER, Role.CADENCE])
    e.devices.add(fake.FakeHeartRate(), [Role.HR])
    ctx = AppContext(engine=e, settings=settings, fake=True)
    pages.register_pages(ctx)
    async with e:
        await asyncio.sleep(0.4)  # let the fake devices connect
        yield ctx


@pytest.fixture
def engine(context):
    """The engine of the app context."""
    return context.engine


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
    monkeypatch.setattr(app.native, "main_window", w)  # the app of this simulation
    await user.open("/")
    user.find(marker="mini").click()
    await user.should_see(marker="mini-expand", retries=20)
    assert w.on_top and w.size == MINI_SIZE
    user.find(marker="mini-expand").click()
    await user.should_see("Elapsed", retries=20)
    assert not w.on_top and w.size == (1400, 900) and w.position == (50, 60)


async def test_start_in_mini(user: User, context):
    context.window.start_mini = True
    await user.open("/")
    await user.should_see(marker="mini-expand", retries=20)
    assert not context.window.start_mini


async def test_history_lists_sessions_and_loads_more(user: User, engine, pages, monkeypatch):
    from smart_trainer.core.recorder import Recorder
    from smart_trainer.core.state import Snapshot

    monkeypatch.setattr(HistoryPage, "PAGE_SIZE", 2)
    for i in range(3):
        now = 1_700_000_000 + i * 3600
        rec = Recorder.create(engine.store.directory, f"Ride {i}", 200, None, now=now)
        rec.record(Snapshot(power=200, cadence=90), 0, None, False, "erg", now=now)
        rec.set_status("finished")
        rec.close()
    await user.open("/history")
    await user.should_see("Ride 2", retries=20)
    await user.should_see("Ride 1")
    await user.should_not_see("Ride 0")
    user.find(marker="history-more").click()
    await user.should_see("Ride 0", retries=20)


async def test_ride_chart_gets_live_points(user: User, engine, pages):
    await user.open("/")
    user.find("Start").click()
    assert engine.ride.state == RideState.RIDING
    for _ in range(40):  # the engine ticks at 1 Hz, the chart refreshes every second
        await asyncio.sleep(0.1)
        assert user.client is not None
        charts = [el for el in user.client.elements.values() if isinstance(el, ui.echart)]
        if charts and len(charts[0].options["series"][1]["data"]) >= 2:  # redraw + append
            break
    else:
        raise AssertionError("the chart never got live power points")


class _OutboxCounter:
    """Counts the element updates and deletions a client sends."""

    def __init__(self, client) -> None:
        self.updated: list[str] = []
        self.deleted = 0
        outbox = client.outbox
        real_update, real_delete = outbox.enqueue_update, outbox.enqueue_delete

        def update(el):
            self.updated.append(type(el).__name__)
            real_update(el)

        def delete(el):
            self.deleted += 1
            real_delete(el)

        outbox.enqueue_update, outbox.enqueue_delete = update, delete


async def test_riding_sends_only_what_changed(user: User, engine):
    """Chips aren't rebuilt and the chart isn't resent: only changed tile values go out."""
    await user.open("/")
    user.find("Start").click()
    await asyncio.sleep(1.5)  # first chart draw and first chip refresh
    assert user.client is not None
    sent = _OutboxCounter(user.client)
    await asyncio.sleep(4.5)  # a few engine ticks, chart syncs and chip refreshes
    assert sent.deleted == 0
    assert "EChart" not in sent.updated  # new points go as a script, not the whole chart
    assert set(sent.updated) <= {"Label", "LinearProgress"}  # tile values and step progress


async def test_device_chip_follows_the_device_state(user: User, engine):
    await user.open("/")
    trainer = engine.devices.device_for(Role.TRAINER)
    assert trainer is not None
    await user.should_see(f"trainer: {trainer.name} (connected)", retries=20)
    trainer.state = ConnState.DISCONNECTED
    await user.should_see(f"trainer: {trainer.name} (disconnected)", retries=40)


def _record_scripts(user: User, monkeypatch) -> list[str]:
    assert user.client is not None
    scripts: list[str] = []
    real = user.client.run_javascript

    def run_javascript(code, **kwargs):
        scripts.append(code)
        return real(code, **kwargs)

    monkeypatch.setattr(user.client, "run_javascript", run_javascript)
    return scripts


@pytest.mark.parametrize("enabled", [True, False])
async def test_interval_cue_beeps_and_shows_the_next_step(
    user: User, engine, pages, monkeypatch, enabled
):
    from smart_trainer.core.workout import Step, Workout

    engine.settings.interval_cues = enabled
    engine.ride.select(Workout("Cue", [Step(6, 0.75), Step(6, 1.0, text="Push")]))
    await user.open("/")
    scripts = _record_scripts(user, monkeypatch)
    user.find("Start").click()
    for _ in range(40):  # the first tick leaves 5 s in the first step: the cue
        await asyncio.sleep(0.1)
        if engine.ride.cue:
            break
    assert engine.ride.cue is not None
    await asyncio.sleep(0.6)  # the page refreshes twice a second
    beeps = [s for s in scripts if "smartTrainerBeep" in s]
    if enabled:
        assert beeps == ["window.smartTrainerBeep?.(2)"]
        await user.should_see("Push · 200 W for 00:06")
    else:
        assert beeps == []
        assert not user.notify.contains("Push")


async def test_opening_a_page_does_not_replay_an_old_cue(user: User, engine, pages, monkeypatch):
    from smart_trainer.core.workout import Step, Workout

    engine.ride.start(Workout("Cue", [Step(6, 0.75), Step(6, 1.0)]))
    for _ in range(40):
        await asyncio.sleep(0.1)
        if engine.ride.cue:
            break
    assert engine.ride.cue is not None
    await user.open("/mini")
    scripts = _record_scripts(user, monkeypatch)
    await asyncio.sleep(1.0)
    assert not [s for s in scripts if "smartTrainerBeep" in s]


async def test_settings_switch_turns_cues_off(user: User, engine, pages):
    engine.settings.interval_cues = True
    await user.open("/settings")
    user.find(marker="interval-cues").click()
    assert engine.settings.model_dump()["interval_cues"] is False


def _cadence_workout():
    import json
    from pathlib import Path

    from smart_trainer.core.workout import parse_intervals_doc

    doc = json.loads((Path(__file__).parent / "data" / "cadence_endurance_doc.json").read_text())
    return parse_intervals_doc(doc, name="Cadence", ftp=200)


async def test_ride_page_shows_cadence_targets(user: User, engine, pages):
    engine.ride.select(_cadence_workout())  # warmup 85 rpm, then 100 / 70 rpm intervals
    await user.open("/")
    user.find("Start").click()
    await user.should_see("Warmup · 85 rpm", retries=20)  # step card
    await user.should_see("target 85 rpm", retries=20)  # cadence tile
    await user.should_see("@ 100 rpm")  # next line
    engine.ride.skip_step()
    await user.should_see("High Cadence · 100 rpm", retries=20)


async def test_cadence_alert_on_ride_and_mini(user: User, engine, pages):
    engine.ride.select(_cadence_workout())
    await user.open("/")
    user.find("Start").click()
    engine.ride.skip_step()
    engine.ride.skip_step()  # "Low Cadence" at 70 rpm; the fake devices ride ~90 rpm
    await user.should_see("Low Cadence · 70 rpm", retries=20)
    for _ in range(80):  # the alert needs 5 s off target
        await asyncio.sleep(0.1)
        if engine.ride.cadence_alert:
            break
    assert engine.ride.cadence_alert == "high"
    await user.should_see("target 70 rpm · ease off", retries=20)
    await user.should_see("HIGH")
    await user.should_see("Cadence high:", retries=20)
    await user.open("/mini")
    await user.should_see("Cadence high:", retries=20)  # replaces the step line


async def test_ride_status_shows_erg_released(user: User, engine, pages, monkeypatch):
    from smart_trainer.core.workout import Step, Workout

    engine.ride.select(Workout("ERG", [Step(600, 1.0)]))
    await user.open("/")
    user.find("Start").click()
    await asyncio.sleep(0.2)
    monkeypatch.setattr(type(engine.controller), "erg_released", property(lambda self: True))
    monkeypatch.setattr(engine.ride, "auto_paused", False)
    assert "ERG released: spin up to 70 rpm" in ride_status(LiveRide.of(engine))
    await user.should_see("ERG released: spin up to 70 rpm", retries=20)


async def test_settings_switch_turns_the_spiral_guard_off(user: User, engine, pages):
    from smart_trainer.core.control import SpiralGuard

    engine.settings.erg_spiral_guard = True
    engine.controller.guard = SpiralGuard()
    await user.open("/settings")
    user.find(marker="erg-spiral-guard").click()
    assert engine.settings.model_dump()["erg_spiral_guard"] is False
    assert engine.controller.guard is None
    user.find(marker="erg-spiral-guard").click()  # and back on
    assert isinstance(engine.controller.guard, SpiralGuard)


async def test_manual_control_sets_the_trainer_target(user: User, engine):
    await user.open("/")
    mode = user.find(marker="manual-mode").elements.pop()
    slider = user.find(marker="manual-slider").elements.pop()
    assert isinstance(mode, ui.toggle) and isinstance(slider, ui.slider)
    mode.value = "slope"
    slider.value = 4.5
    assert engine.controller.mode == Mode.SLOPE
    assert engine.controller.target_for(Mode.SLOPE) == 4.5


async def test_settings_save_updates_profile_and_power_matching(user: User, engine, pages):
    settings = engine.settings
    settings.sync_profile = False
    power_match = engine.controller.matcher.cfg
    await user.open("/settings")
    for marker, value in (("ftp", 265), ("max-offset", 45)):
        field = user.find(marker=marker).elements.pop()
        assert isinstance(field, ui.number)
        field.value = value
    user.find(marker="save-settings").click()
    await user.should_see("Settings saved")
    assert settings.ftp == 265
    assert power_match.max_offset_w == 45  # the controller sees the change right away


async def test_start_rides_the_selected_workout(user: User, engine):
    from smart_trainer.core.workout import Step, Workout

    workout = Workout("Picked", [Step(600, 0.6)])
    engine.ride.select(workout)
    await user.open("/workouts")
    await user.should_see("Selected: Picked")
    await user.open("/")
    await user.should_see("Picked")
    user.find("Start").click()
    await user.should_see("Started: Picked")
    assert engine.ride.runner and engine.ride.runner.workout is workout


async def test_strava_callback_route_is_registered(user: User, context, pages):
    paths = {getattr(route, "path", None) for route in app.routes}
    assert connections.STRAVA_CALLBACK in paths


async def test_strava_callback_rejects_an_unknown_request(user: User, context, pages):
    response = await connections.finish_strava_authorization(
        context.settings, "never-sent", "code", "", "activity:write"
    )
    assert response.status_code == 400
    assert "Strava connection failed" in bytes(response.body).decode()


async def test_live_ride_reads_the_latest_snapshot(tmp_path, settings):
    e = Engine.from_settings(settings, sessions=tmp_path, run_loop=False)
    live = LiveRide.of(e)
    before = live.snapshot()
    await e.tick(now_wall=1_700_000_000, now_mono=1.0)  # publishes a new snapshot
    assert live.snapshot() is e.snapshot and live.snapshot() is not before
