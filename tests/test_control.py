from typing import cast

import pytest

from smart_trainer.ble.device import ControllableTrainer
from smart_trainer.config import PowerMatch
from smart_trainer.core.control import (
    SPIRAL_RELEASE_SLOPE,
    Mode,
    PowerMatcher,
    SpiralGuard,
    TrainerController,
)
from smart_trainer.core.shifting import NEUTRAL_GEAR, VirtualGearbox
from smart_trainer.core.state import Snapshot


def simulate(bias: float, target: float, seconds: int = 240, matcher=None):
    """Trainer reads `bias` x true power and drives its own reading to the command."""
    power_matcher = matcher or PowerMatcher(PowerMatch())
    true_power, cmd = target, target
    for second in range(seconds):
        true_power += (cmd / bias - true_power) * 0.5
        snap = Snapshot(
            power=true_power,
            pedal_power=true_power,
            cadence=90,
            pedal_avg=true_power,
            trainer_avg=true_power * bias,
        )
        cmd = power_matcher.command(target, snap, now=float(second))
    return true_power, power_matcher


def test_power_match_converges_to_pedal_target():
    power, matcher = simulate(1.05, 200)
    assert abs(power - 200) < 2
    assert abs(matcher.ratio - 1.05) < 0.01


def test_power_match_tracks_target_change_quickly():
    _, matcher = simulate(1.05, 200)
    # After learning, a new target is matched almost immediately via the ratio
    power, _ = simulate(1.05, 300, seconds=8, matcher=matcher)
    assert abs(power - 300) < 5


def test_offset_is_clamped():
    cfg = PowerMatch(max_offset_w=10)
    power, matcher = simulate(1.25, 200, matcher=PowerMatcher(cfg))
    assert power < 200  # can't fully compensate a 25 % error with a 10 W clamp
    snap = Snapshot(cadence=90, pedal_power=150, pedal_avg=150, trainer_avg=190)
    cmd = matcher.command(200, snap, now=1000.0)
    assert cmd <= 210


def test_no_learning_when_not_pedaling():
    matcher = PowerMatcher(PowerMatch())
    for second in range(60):
        matcher.command(
            200, Snapshot(cadence=0, pedal_power=0, pedal_avg=0, trainer_avg=0), now=float(second)
        )
    assert matcher.ratio == 1.0 and matcher.integral == 0.0


# ------------------------------------------------------------------ targets
@pytest.mark.parametrize(
    ("mode", "value", "command"),
    [
        (Mode.ERG, 220.0, ("erg", 220)),
        (Mode.RESISTANCE, 40.0, ("resistance", 40.0)),
        (Mode.SLOPE, 3.5, ("slope", 3.5)),
    ],
)
async def test_set_target_switches_mode_and_sends_its_value(
    controller, trainer, mode, value, command
):
    controller.set_target(mode, value)
    await controller.apply(Snapshot(cadence=90, power=100), connected=True, now=0.0)
    assert controller.mode == mode and controller.target_for(mode) == value
    assert _last(trainer) == command


def test_each_mode_keeps_its_own_target():
    controller = TrainerController(PowerMatch())
    controller.set_target(Mode.SLOPE, 2.0)
    controller.set_target(Mode.ERG, 300.0)
    controller.set_target(
        Mode.SLOPE, controller.target_for(Mode.SLOPE)
    )  # back to slope: its target is kept
    assert controller.target_for(Mode.SLOPE) == 2.0 and controller.target_for(Mode.ERG) == 300.0


# ------------------------------------------------------------------ ERG spiral guard
class _Trainer:
    """Records the commands the controller sends."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, float]] = []

    async def set_erg(self, watts: float) -> None:
        self.sent.append(("erg", watts))

    async def set_resistance(self, pct: float) -> None:
        self.sent.append(("resistance", pct))

    async def set_slope(self, grade: float) -> None:
        self.sent.append(("slope", grade))


@pytest.fixture
def trainer() -> _Trainer:
    """A trainer that records the commands it gets."""
    return _Trainer()


@pytest.fixture
def controller(trainer) -> TrainerController:
    """ERG at 250 W on trainer power, with the spiral guard on."""
    controller = TrainerController(PowerMatch(), SpiralGuard())
    controller.trainer = cast(ControllableTrainer, trainer)
    controller.use_pedals = False  # trainer power: the ERG command is the target itself
    controller.set_target(Mode.ERG, 250)
    return controller


async def _ride(
    controller: TrainerController, cadences: list[float | None], t0: float = 0.0
) -> float:
    """One apply per second at the given cadences; returns the next time."""
    now = t0
    for cad in cadences:
        await controller.apply(Snapshot(cadence=cad, power=100), connected=True, now=now)
        now += 1.0
    return now


def _last(trainer: _Trainer) -> tuple[str, float]:
    return trainer.sent[-1]


@pytest.mark.parametrize(
    ("guard", "cadences"),
    [
        (True, [90, 40, 40, 40, 90, 90]),  # under 50 rpm for 2 s only (t=1..3)
        (True, [None] * 10),  # no cadence reading: never release
        (False, [20] * 10),  # guard off: hold the target however slow
    ],
)
async def test_erg_target_is_held(controller, trainer, guard, cadences):
    if not guard:
        controller.set_spiral_guard(None)
    await _ride(controller, cadences)
    assert not controller.erg_released
    assert set(trainer.sent) == {("erg", 250)}


async def test_collapsed_cadence_releases_erg_to_a_flat_road(controller, trainer):
    now = await _ride(controller, [90, 45, 45, 45])  # under 50 since t=1: 2 s at t=3
    assert not controller.erg_released
    await _ride(controller, [45], now)  # 3 s at t=4
    assert controller.erg_released
    assert _last(trainer) == ("slope", SPIRAL_RELEASE_SLOPE)
    assert (
        controller.mode == Mode.ERG and controller.erg_target == 250
    )  # the ride's target is untouched


async def test_erg_resumes_only_after_spinning_up(controller, trainer):
    now = await _ride(controller, [30, 30, 30, 30])
    assert controller.erg_released
    now = await _ride(
        controller, [60, 60, 60, 60, 60], now
    )  # between the thresholds: stays released
    assert controller.erg_released
    now = await _ride(controller, [75, 75, 50, 75, 75], now)  # interrupted: the 2 s start over
    assert controller.erg_released
    await _ride(controller, [75], now)
    assert not controller.erg_released
    assert _last(trainer) == ("erg", 250)  # straight back to the full target


async def test_missing_cadence_keeps_a_release(controller):
    now = await _ride(controller, [30, 30, 30, 30])
    await _ride(controller, [None] * 5, now)
    assert controller.erg_released


async def test_turning_the_guard_off_resumes_erg(controller, trainer):
    now = await _ride(controller, [30, 30, 30, 30])
    assert controller.erg_released
    controller.set_spiral_guard(None)
    await _ride(controller, [30], now)
    assert not controller.erg_released and _last(trainer) == ("erg", 250)


async def test_leaving_erg_clears_the_release(controller, trainer):
    now = await _ride(controller, [30, 30, 30, 30])
    assert controller.erg_released
    controller.set_mode(Mode.RESISTANCE)
    now = await _ride(controller, [30], now)
    assert not controller.erg_released and _last(trainer)[0] == "resistance"
    controller.set_mode(Mode.ERG)  # back in ERG: a fresh 3 s are needed before releasing again
    await _ride(controller, [30, 30], now)
    assert not controller.erg_released and _last(trainer) == ("erg", 250)


async def test_power_matching_restarts_clean_after_a_release(controller):
    controller.use_pedals = True
    controller.matcher.integral = 40.0  # built up while grinding under target
    controller.matcher.ratio = 1.05
    now = await _ride(controller, [30, 30, 30, 30])
    assert controller.erg_released
    await _ride(controller, [80, 80, 80], now)
    assert not controller.erg_released
    assert controller.matcher.integral == 0.0
    assert controller.matcher.ratio == 1.05  # the learned calibration is kept


# ------------------------------------------------------------------ virtual shifting
@pytest.fixture
def geared(trainer) -> TrainerController:
    """Slope mode at 2 % with virtual shifting on, in the real gear."""
    controller = TrainerController(PowerMatch(), gearbox=VirtualGearbox(rider_kg=lambda: 75.0))
    controller.trainer = cast(ControllableTrainer, trainer)
    controller.set_target(Mode.SLOPE, 2.0)
    return controller


async def test_slope_is_sent_for_the_virtual_gear(geared, trainer):
    snap = Snapshot(cadence=90, power=200, speed_kmh=30.0)
    await geared.apply(snap, connected=True, now=0.0)
    assert _last(trainer) == ("slope", 2.0)  # real gear: the road grade itself
    geared.shift(2)
    await geared.apply(snap, connected=True, now=1.0)
    expected = VirtualGearbox(rider_kg=lambda: 75.0, gear=NEUTRAL_GEAR + 2).grade_for(2.0, 30.0)
    assert _last(trainer) == ("slope", round(expected, 1))
    assert expected > 2.0


@pytest.mark.parametrize(("mode", "value"), [(Mode.ERG, 220.0), (Mode.RESISTANCE, 40.0)])
async def test_gears_only_exist_in_slope_mode(geared, trainer, mode, value):
    geared.set_target(mode, value)
    assert not geared.shifting
    geared.shift(3)
    await geared.apply(Snapshot(cadence=90, power=200, speed_kmh=30.0), connected=True, now=0.0)
    assert _last(trainer) == (mode.value, value)
    geared.set_target(Mode.SLOPE, 2.0)
    assert geared.gearbox is not None and geared.gearbox.gear == NEUTRAL_GEAR


async def test_without_a_gearbox_shifting_does_nothing(trainer):
    controller = TrainerController(PowerMatch())
    controller.trainer = cast(ControllableTrainer, trainer)
    controller.set_target(Mode.SLOPE, 2.0)
    controller.shift(2)
    await controller.apply(Snapshot(cadence=90, speed_kmh=30.0), connected=True, now=0.0)
    assert not controller.shifting and _last(trainer) == ("slope", 2.0)


def test_turning_shifting_off_and_on_starts_in_the_real_gear(geared):
    geared.shift(4)
    geared.set_gearbox(None)
    assert not geared.shifting
    geared.set_gearbox(VirtualGearbox(rider_kg=lambda: 75.0))
    assert geared.gearbox is not None and geared.gearbox.gear == NEUTRAL_GEAR
