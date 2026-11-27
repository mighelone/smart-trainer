"""UI tests with NiceGUI's simulated user (no browser): pages and the mini overlay."""

import asyncio
import sys

import pytest
from nicegui import app, ui
from nicegui.testing import User

from smart_trainer.ble import fake
from smart_trainer.ble.parsers.zwift import Button
from smart_trainer.core.control import Mode
from smart_trainer.core.engine import Engine
from smart_trainer.core.ride import RideState
from smart_trainer.core.state import ConnState, Role
from smart_trainer.ui.context import AppContext, LiveRide
from smart_trainer.ui.pages.history import HistoryPage
from smart_trainer.ui.pages.live import ride_status
from smart_trainer.ui.strava_auth import StravaAuthorization
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

    def move(self, left: int, top: int) -> None:
        self.position = (left, top)


@pytest.fixture
def pages(user: User):
    """The pages module imported for this test's simulation (fresh in every test)."""
    return sys.modules["smart_trainer.ui.pages"]


@pytest.fixture
async def context(pages, tmp_path, settings):
    """App context with simulated devices and no connections; its pages are registered."""
    engine = Engine.from_settings(settings, sessions=tmp_path)
    engine.devices.add(fake.FakeTrainer(), [Role.TRAINER, Role.SPEED])
    engine.devices.add(fake.FakePowerMeter(), [Role.POWER, Role.CADENCE])
    engine.devices.add(fake.FakeHeartRate(), [Role.HR])
    strava = StravaAuthorization(settings, 8080, open_url=lambda url: None)
    ctx = AppContext(engine=engine, settings=settings, strava=strava, fake=True)
    pages.register_pages(ctx)
    async with engine:
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
    window = FakeWindow()
    monkeypatch.setattr(app.native, "main_window", window)  # the app of this simulation
    await user.open("/")
    user.find(marker="mini").click()
    await user.should_see(marker="mini-expand", retries=20)
    assert window.on_top and window.size == MINI_SIZE
    user.find(marker="mini-expand").click()
    await user.should_see("Elapsed", retries=20)
    assert not window.on_top and window.size == (1400, 900) and window.position == (50, 60)


async def test_start_in_mini(user: User, context):
    context.window.start_mini = True
    await user.open("/")
    await user.should_see(marker="mini-expand", retries=20)
    assert not context.window.start_mini


async def test_history_lists_sessions_and_loads_more(user: User, engine, pages, monkeypatch):
    from smart_trainer.core.recorder import Recorder
    from smart_trainer.core.state import Snapshot

    monkeypatch.setattr(HistoryPage, "PAGE_SIZE", 2)
    for index in range(3):
        now = 1_700_000_000 + index * 3600
        rec = Recorder.create(engine.store.directory, f"Ride {index}", 200, None, now=now)
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


async def test_ride_status_says_when_the_click_is_locked(user: User, engine, pages):
    from smart_trainer.ble.zwift_click import LOCKED

    click = fake.FakeClick()
    engine.devices.add(click, [Role.SHIFTER])
    click.lock()
    assert LOCKED in ride_status(LiveRide.of(engine))
    await user.open("/")
    await user.should_see(LOCKED, retries=20)
    click.press(Button.PLUS)
    assert LOCKED not in ride_status(LiveRide.of(engine))


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


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (Mode.ERG, {"min": 50, "max": 600, "step": 5}),
        (Mode.SLOPE, {"min": -10, "max": 20, "step": 0.5}),
        (Mode.RESISTANCE, {"min": 0, "max": 100, "step": 1}),
    ],
)
async def test_manual_slider_range_is_numeric_for_each_mode(user: User, engine, mode, expected):
    # Quasar computes `min + ratio * (max - min)` while dragging: a string min turns that
    # into string concatenation and the slider throws, freezing the page mid-drag
    await user.open("/")
    toggle = user.find(marker="manual-mode").elements.pop()
    slider = user.find(marker="manual-slider").elements.pop()
    assert isinstance(toggle, ui.toggle) and isinstance(slider, ui.slider)
    toggle.value = "slope" if mode == Mode.RESISTANCE else mode.value  # force a real change
    toggle.value = mode.value
    assert {key: slider.props[key] for key in expected} == expected
    assert all(isinstance(slider.props[key], (int, float)) for key in expected)


async def test_manual_slider_opens_with_the_range_of_the_current_mode(user: User, engine):
    engine.controller.set_target(Mode.ERG, 200)
    await user.open("/")
    slider = user.find(marker="manual-slider").elements.pop()
    assert isinstance(slider, ui.slider)
    assert (slider.props["min"], slider.props["max"], slider.value) == (50, 600, 200)


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


async def test_live_ride_reads_the_latest_snapshot(tmp_path, settings):
    engine = Engine.from_settings(settings, sessions=tmp_path, run_loop=False)
    live = LiveRide.of(engine)
    before = live.snapshot()
    await engine.tick(now_wall=1_700_000_000, now_mono=1.0)  # publishes a new snapshot
    assert live.snapshot() is engine.snapshot and live.snapshot() is not before


async def test_mini_shows_the_workout_step_and_changes_intensity(user: User, engine):
    from smart_trainer.core.workout import Step, Workout

    engine.ride.start(Workout("Mini", [Step(600, 0.6), Step(300, 1.0)]))
    await user.open("/mini")
    await user.should_see("1/2 · 100%", retries=20)  # interval tile
    await user.should_see("of 15:00")  # step line: elapsed of the workout duration
    user.find(marker="mini-plus").click()
    assert engine.ride.runner and engine.ride.runner.intensity == pytest.approx(1.05)
    await user.should_see("1/2 · 105%", retries=20)


async def test_mini_hides_intensity_in_a_free_ride(user: User, engine):
    await user.open("/mini")
    user.find(marker="mini-start").click()
    await user.should_see("free ride", retries=20)
    await user.should_not_see(marker="mini-plus")


async def test_ride_tiles_show_source_command_and_heart_rate(user: User, engine):
    from smart_trainer.core.workout import Step, Workout

    engine.settings.update(max_hr=200)
    engine.ride.select(Workout("Tiles", [Step(600, 1.0)]))
    await user.open("/")
    user.find("Start").click()
    await user.should_see("from pedals", retries=30)  # power tile
    await user.should_see("trainer command 200 W", retries=30)  # target tile
    await user.should_see("of max", retries=30)  # heart-rate tile


def _unfinished_session(directory) -> None:
    """Leave a session still marked active, as after a crash."""
    from smart_trainer.core.recorder import Recorder
    from smart_trainer.core.state import Snapshot

    rec = Recorder.create(directory, "Crashed", 200, None, now=1_700_000_000)
    rec.record(Snapshot(power=200, cadence=90), 0, None, False, "erg", now=1_700_000_000)
    rec.close()


async def test_recovery_dialog_resumes_an_unfinished_session(user: User, engine):
    _unfinished_session(engine.store.directory)
    await user.open("/")
    await user.should_see("Unfinished session found", retries=20)
    user.find("Resume").click()
    await user.should_see("Session resumed: start pedalling", retries=20)
    assert engine.ride.state == RideState.RIDING


async def test_recovery_dialog_discards_an_unfinished_session(user: User, engine):
    _unfinished_session(engine.store.directory)
    await user.open("/")
    await user.should_see("Unfinished session found", retries=20)
    user.find("Discard").click()
    await user.should_see("Session moved to sessions/discarded", retries=20)
    assert not engine.ride.recoverable_sessions()
    assert list((engine.store.directory / "discarded").iterdir())


def _charts(user: User) -> list[ui.echart]:
    assert user.client is not None
    return [el for el in user.client.elements.values() if isinstance(el, ui.echart)]


async def test_workout_chart_draws_the_target_profile(user: User, engine):
    from smart_trainer.core.workout import Step, Workout

    engine.ride.select(Workout("Profile", [Step(300, 0.5), Step(120, 1.0)]))
    await user.open("/")
    await user.should_see("Profile", retries=20)
    [chart] = _charts(user)
    opts = chart.options
    assert opts["xAxis"]["max"] == 420
    assert [s["name"] for s in opts["series"]] == ["Target", "Power", "HR"]
    assert opts["series"][0]["data"][0] == [0, 100]  # 50 % of a 200 W FTP
    assert len(opts["yAxis"]) == 2


async def test_workout_preview_chart_is_compact(user: User, engine):
    await user.open("/workouts")
    await user.should_see("Workouts", retries=20)
    previews = _charts(user)
    assert previews
    for chart in previews:
        assert [s["name"] for s in chart.options["series"]] == ["Target"]
        assert len(chart.options["yAxis"]) == 1


async def test_gear_buttons_shift_in_slope_mode(user: User, engine):
    engine.set_virtual_shifting(True)
    engine.controller.set_target(Mode.SLOPE, 2.0)
    await user.open("/")
    await user.should_see(marker="gear", retries=20)
    await user.should_see("Gear 8/15", retries=20)
    user.find(marker="shift-up").click()
    user.find(marker="shift-up").click()
    assert engine.controller.gearbox is not None and engine.controller.gearbox.gear == 10
    await user.should_see("Gear 10/15", retries=20)
    user.find(marker="shift-down").click()
    await user.should_see("Gear 9/15", retries=20)


async def test_shift_buttons_are_hidden_in_erg_mode(user: User, engine):
    engine.set_virtual_shifting(True)
    engine.controller.set_target(Mode.ERG, 200.0)
    await user.open("/")
    await asyncio.sleep(0.6)  # let the refresh timer run
    await user.should_not_see(marker="shift-up")


@pytest.mark.parametrize("virtual_shifting", [False, True])
async def test_shift_buttons_step_the_resistance(user: User, engine, virtual_shifting):
    engine.set_virtual_shifting(virtual_shifting)
    engine.controller.set_target(Mode.RESISTANCE, 25.0)
    await user.open("/")
    await user.should_see(marker="shift-up", retries=20)
    user.find(marker="shift-up").click()
    await user.should_see("30%", retries=20)
    await user.should_not_see(marker="gear")
    engine.controller.shift(-2)  # as a Click press does, outside the page
    await user.should_see("20%", retries=20)
    slider = user.find(marker="manual-slider").elements.pop()
    assert isinstance(slider, ui.slider)
    for _ in range(20):
        if slider.value == 20:
            break
        await asyncio.sleep(0.1)
    assert slider.value == 20 and engine.controller.target_for(Mode.RESISTANCE) == 20


@pytest.mark.parametrize(("keys", "gear"), [("+", 9), ("=", 9), ("-", 7), ("++-", 9), ("x", 8)])
async def test_keys_shift_gears(user: User, engine, keys, gear):
    engine.set_virtual_shifting(True)
    engine.controller.set_target(Mode.SLOPE, 2.0)
    await user.open("/")
    keyboard = user.find(ui.keyboard)
    for key in keys:
        for action in ("keydown", "keyup"):  # one press: only keydown shifts
            keyboard.trigger("key", _key_event(key, action))
    assert engine.controller.gearbox is not None and engine.controller.gearbox.gear == gear


def _key_event(key: str, action: str) -> dict:
    """The arguments ``ui.keyboard`` gets from the browser for a key event."""
    return {
        "action": action,
        "repeat": False,
        "key": key,
        "code": "",
        "location": 0,
        "altKey": False,
        "ctrlKey": False,
        "metaKey": False,
        "shiftKey": False,
    }


async def test_settings_switch_turns_virtual_shifting_on(user: User, engine, pages):
    await user.open("/settings")
    user.find(marker="virtual-shifting").click()
    assert engine.settings.model_dump()["virtual_shifting"] is True
    assert engine.controller.gearbox is not None
    user.find(marker="virtual-shifting").click()  # and back off
    assert engine.controller.gearbox is None


@pytest.mark.parametrize(
    ("zwift_type", "note"),
    [
        (0x0B, "relays both controllers: needs a Zwift unlock every 24 h"),
        (0x0A, "right controller: + harder, B easier, works without Zwift (recommended)"),
    ],
)
async def test_scan_pairs_a_click_as_shifter(user: User, engine, monkeypatch, zwift_type, note):
    from smart_trainer.ble import scanner
    from smart_trainer.ble.device import DriverId

    async def scan(timeout: float) -> list[scanner.Discovered]:
        # the fake driver, so pairing doesn't reach for Bluetooth
        return [
            scanner.Discovered("click-1", "Zwift Click", -48, [DriverId.FAKE_CLICK], zwift_type)
        ]

    monkeypatch.setattr(scanner, "scan", scan)
    await user.open("/devices")
    user.find("Scan").click()
    await user.should_see(note, retries=20)
    user.find("Use as shifter").click()
    assert engine.devices.assignment.get(Role.SHIFTER) == "click-1"
    assert engine.settings.devices["click-1"].driver == DriverId.FAKE_CLICK
