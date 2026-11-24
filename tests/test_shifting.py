import pytest

from smart_trainer.ble.parsers.ftms import SIM_MAX_GRADE_PCT
from smart_trainer.core.shifting import (
    GEAR_COUNT,
    NEUTRAL_GEAR,
    VirtualGearbox,
    equivalent_grade,
    gear_ratio,
)

RIDER_KG = 75.0
MASS_KG = 84.0  # rider + bike


@pytest.fixture
def gearbox() -> VirtualGearbox:
    """A gearbox in the real gear for a 75 kg rider."""
    return VirtualGearbox(rider_kg=lambda: RIDER_KG)


@pytest.mark.parametrize(
    ("gear", "ratio"),
    [(NEUTRAL_GEAR, 1.0), (NEUTRAL_GEAR + 1, 1.1), (1, 0.513), (GEAR_COUNT, 1.949)],
)
def test_gear_ratio_is_relative_to_the_real_gear(gear, ratio):
    assert gear_ratio(gear) == pytest.approx(ratio, abs=0.001)


@pytest.mark.parametrize("grade", [-8.0, 0.0, 4.5, 12.0])
@pytest.mark.parametrize("speed_mps", [0.0, 5.0, 12.0])
def test_the_real_gear_sends_the_road_grade(grade, speed_mps):
    assert equivalent_grade(grade, 1.0, speed_mps, MASS_KG) == pytest.approx(grade)


@pytest.mark.parametrize(
    ("grade", "ratio", "speed_mps", "expected"),
    [
        # flat at 36 km/h, one gear up: 1.1·0.004 − 0.004 + 0.51·10²·(1.1³ − 1)/(84·9.81)
        (0.0, 1.1, 10.0, 2.09),
        # three gears up: the air drag grows with k³
        (0.0, 1.331, 10.0, 8.54),
        # one gear down on the flat: less drag than the real gear
        (0.0, 1 / 1.1, 10.0, -1.58),
        # 5 % climb, two gears up, no speed: only the climbing part, 1.21·0.054 − 0.004
        (5.0, 1.21, 0.0, 6.13),
    ],
)
def test_equivalent_grade_matches_the_virtual_road(grade, ratio, speed_mps, expected):
    assert equivalent_grade(grade, ratio, speed_mps, MASS_KG) == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize(
    ("grade", "ratio", "speed_mps", "expected"),
    [
        (15.0, 1.95, 11.0, SIM_MAX_GRADE_PCT),
        (-20.0, 1.95, 0.0, -SIM_MAX_GRADE_PCT),
    ],
)
def test_equivalent_grade_is_clamped_to_what_ftms_accepts(grade, ratio, speed_mps, expected):
    assert equivalent_grade(grade, ratio, speed_mps, MASS_KG) == expected


def test_harder_gears_send_steeper_grades(gearbox):
    grades = []
    for gear in range(1, GEAR_COUNT + 1):
        gearbox.gear = gear
        grades.append(gearbox.grade_for(1.0, 20.0))  # slow enough that no gear is clamped
    assert grades == sorted(grades) and len(set(grades)) == GEAR_COUNT


@pytest.mark.parametrize(
    ("start", "steps", "expected"),
    [
        (NEUTRAL_GEAR, 1, NEUTRAL_GEAR + 1),
        (NEUTRAL_GEAR, -2, NEUTRAL_GEAR - 2),
        (GEAR_COUNT, 1, GEAR_COUNT),  # already in the hardest gear
        (1, -1, 1),  # already in the easiest gear
        (NEUTRAL_GEAR, -20, 1),
    ],
)
def test_shifting_stops_at_the_first_and_last_gear(gearbox, start, steps, expected):
    gearbox.gear = start
    gearbox.shift(steps)
    assert gearbox.gear == expected


def test_a_new_gearbox_is_in_the_real_gear(gearbox):
    assert gearbox.gear == NEUTRAL_GEAR
    assert gearbox.grade_for(3.0, 30.0) == pytest.approx(3.0)


def test_grade_for_ignores_speed_noise(gearbox):
    gearbox.shift(2)
    assert gearbox.grade_for(0.0, 30.3) == gearbox.grade_for(0.0, 29.8)


def test_grade_for_without_speed_keeps_only_the_climbing_part(gearbox):
    gearbox.shift(2)
    assert gearbox.grade_for(5.0, None) == pytest.approx(
        equivalent_grade(5.0, gear_ratio(NEUTRAL_GEAR + 2), 0.0, MASS_KG)
    )


def test_grade_for_reads_the_rider_weight_on_every_command():
    weight = {"kg": 75.0}
    gearbox = VirtualGearbox(rider_kg=lambda: weight["kg"])
    gearbox.shift(1)
    heavier_rider = gearbox.grade_for(0.0, 36.0)
    weight["kg"] = 55.0
    # the same air drag weighs more against a lighter rider
    assert gearbox.grade_for(0.0, 36.0) > heavier_rider
