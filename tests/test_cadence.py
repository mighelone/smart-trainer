"""Cadence targets: the step's target, and an alert when off it for a few seconds."""

import pytest

from smart_trainer.config import Settings
from smart_trainer.core.engine import Engine
from smart_trainer.core.ride import CADENCE_WARN_AFTER_S
from smart_trainer.core.state import Snapshot
from smart_trainer.core.workout import Step, Workout
from smart_trainer.ui.widgets import (
    CADENCE_ALERT_COLOR,
    cadence_display,
    next_step_text,
    step_title,
)

T0 = 1_700_000_000
TWO_STEPS = Workout("C", [Step(60, 0.6, cadence=90), Step(60, 0.8, cadence=100), Step(30, 0.5)])


@pytest.fixture
def ride(tmp_path):
    e = Engine.from_settings(Settings(ftp=200, auto_upload=False), sessions=tmp_path)
    yield e.ride
    e.ride.close()


def _ride(ride, seconds: int, cadence: float | None) -> list:
    """Ride ``seconds`` 1 s ticks at a cadence; return the alert after each tick."""
    alerts = []
    for _ in range(seconds):
        ride.tick(Snapshot(power=200, cadence=cadence), T0)
        alerts.append(ride.cadence_alert)
    return alerts


def test_cadence_target_follows_the_step(ride):
    ride.start(TWO_STEPS)
    _ride(ride, 1, 90)
    assert ride.cadence_target == 90
    ride.skip_step()
    _ride(ride, 1, 90)
    assert ride.cadence_target == 100
    ride.skip_step()
    _ride(ride, 1, 90)
    assert ride.cadence_target is None  # this step has no target


def test_no_target_in_a_free_ride(ride):
    ride.start(None)
    assert _ride(ride, 10, 50) == [None] * 10 and ride.cadence_target is None


def test_low_alert_after_five_seconds_off_target(ride):
    ride.start(TWO_STEPS)
    alerts = _ride(ride, 7, 75)  # 15 rpm under 90
    n = int(CADENCE_WARN_AFTER_S)
    assert alerts == [None] * (n - 1) + ["low"] * (7 - n + 1)


def test_high_alert(ride):
    ride.start(TWO_STEPS)
    assert _ride(ride, 5, 105)[-1] == "high"


def test_within_tolerance_never_alerts(ride):
    ride.start(TWO_STEPS)
    assert _ride(ride, 20, 81) == [None] * 20  # 9 under
    assert _ride(ride, 20, 100) == [None] * 20  # 10 over: still within


def test_alert_clears_when_back_on_target(ride):
    ride.start(TWO_STEPS)
    _ride(ride, 6, 70)
    assert ride.cadence_alert == "low"
    assert _ride(ride, 1, 88) == [None]
    assert _ride(ride, 3, 70) == [None] * 3  # the 5 s count starts again


def test_a_short_dip_doesnt_alert(ride):
    ride.start(TWO_STEPS)
    for _ in range(3):
        assert _ride(ride, 3, 70) == [None] * 3
        _ride(ride, 1, 90)


def test_new_step_starts_with_a_clean_slate(ride):
    ride.start(TWO_STEPS)
    _ride(ride, 4, 75)  # 4 s low in the 90 rpm step
    ride.skip_step()  # 100 rpm step: 75 is still low, but the count restarts
    alerts = _ride(ride, 5, 75)
    assert alerts == [None] * 4 + ["low"]


def test_no_alert_while_paused(ride):
    ride.start(TWO_STEPS)
    _ride(ride, 6, 70)
    ride.toggle_pause()
    assert _ride(ride, 3, 70) == [None] * 3
    ride.toggle_pause()
    assert _ride(ride, 4, 70) == [None] * 4  # counting again from zero


def test_cadence_display():
    assert cadence_display(None, None) == ("", None, None)
    assert cadence_display(90, None) == ("target 90 rpm", None, None)
    assert cadence_display(90, "low") == ("target 90 rpm · speed up", CADENCE_ALERT_COLOR, "LOW")
    assert cadence_display(90, "high") == ("target 90 rpm · ease off", CADENCE_ALERT_COLOR, "HIGH")


def test_step_texts_show_the_cadence_target():
    assert (
        step_title(0, Step(60, 1.0, text="High Cadence", cadence=100)) == "High Cadence · 100 rpm"
    )
    assert step_title(2, Step(60, 1.0)) == "Interval 3"
    assert next_step_text(Step(30, 2.0, text="Sprint", cadence=110), 200) == (
        "Sprint · 400 W for 00:30 @ 110 rpm"
    )
