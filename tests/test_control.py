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
from smart_trainer.core.state import Snapshot


def simulate(bias: float, target: float, seconds: int = 240, matcher=None):
    """Trainer reads `bias` x true power and drives its own reading to the command."""
    m = matcher or PowerMatcher(PowerMatch())
    true_power, cmd = target, target
    for t in range(seconds):
        true_power += (cmd / bias - true_power) * 0.5
        snap = Snapshot(
            power=true_power,
            pedal_power=true_power,
            cadence=90,
            pedal_avg=true_power,
            trainer_avg=true_power * bias,
        )
        cmd = m.command(target, snap, now=float(t))
    return true_power, m


def test_power_match_converges_to_pedal_target():
    p, m = simulate(1.05, 200)
    assert abs(p - 200) < 2
    assert abs(m.ratio - 1.05) < 0.01


def test_power_match_tracks_target_change_quickly():
    _, m = simulate(1.05, 200)
    # After learning, a new target is matched almost immediately via the ratio
    p, _ = simulate(1.05, 300, seconds=8, matcher=m)
    assert abs(p - 300) < 5


def test_offset_is_clamped():
    cfg = PowerMatch(max_offset_w=10)
    p, m = simulate(1.25, 200, matcher=PowerMatcher(cfg))
    assert p < 200  # can't fully compensate a 25 % error with a 10 W clamp
    snap = Snapshot(cadence=90, pedal_power=150, pedal_avg=150, trainer_avg=190)
    cmd = m.command(200, snap, now=1000.0)
    assert cmd <= 210


def test_no_learning_when_not_pedaling():
    m = PowerMatcher(PowerMatch())
    for t in range(60):
        m.command(200, Snapshot(cadence=0, pedal_power=0, pedal_avg=0, trainer_avg=0), now=float(t))
    assert m.ratio == 1.0 and m.integral == 0.0


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
    c = controller
    c.set_target(mode, value)
    await c.apply(Snapshot(cadence=90, power=100), connected=True, now=0.0)
    assert c.mode == mode and c.target_for(mode) == value
    assert _last(trainer) == command


def test_each_mode_keeps_its_own_target():
    c = TrainerController(PowerMatch())
    c.set_target(Mode.SLOPE, 2.0)
    c.set_target(Mode.ERG, 300.0)
    c.set_target(Mode.SLOPE, c.target_for(Mode.SLOPE))  # back to slope: its target is kept
    assert c.target_for(Mode.SLOPE) == 2.0 and c.target_for(Mode.ERG) == 300.0


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
    c = TrainerController(PowerMatch(), SpiralGuard())
    c.trainer = cast(ControllableTrainer, trainer)
    c.use_pedals = False  # trainer power: the ERG command is the target itself
    c.set_target(Mode.ERG, 250)
    return c


async def _ride(c: TrainerController, cadences: list[float | None], t0: float = 0.0) -> float:
    """One apply per second at the given cadences; returns the next time."""
    t = t0
    for cad in cadences:
        await c.apply(Snapshot(cadence=cad, power=100), connected=True, now=t)
        t += 1.0
    return t


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
    c = controller
    t = await _ride(c, [90, 45, 45, 45])  # under 50 since t=1: 2 s at t=3
    assert not c.erg_released
    await _ride(c, [45], t)  # 3 s at t=4
    assert c.erg_released
    assert _last(trainer) == ("slope", SPIRAL_RELEASE_SLOPE)
    assert c.mode == Mode.ERG and c.erg_target == 250  # the ride's target is untouched


async def test_erg_resumes_only_after_spinning_up(controller, trainer):
    c = controller
    t = await _ride(c, [30, 30, 30, 30])
    assert c.erg_released
    t = await _ride(c, [60, 60, 60, 60, 60], t)  # between the thresholds: stays released
    assert c.erg_released
    t = await _ride(c, [75, 75, 50, 75, 75], t)  # interrupted: the 2 s start over
    assert c.erg_released
    await _ride(c, [75], t)
    assert not c.erg_released
    assert _last(trainer) == ("erg", 250)  # straight back to the full target


async def test_missing_cadence_keeps_a_release(controller):
    c = controller
    t = await _ride(c, [30, 30, 30, 30])
    await _ride(c, [None] * 5, t)
    assert c.erg_released


async def test_turning_the_guard_off_resumes_erg(controller, trainer):
    c = controller
    t = await _ride(c, [30, 30, 30, 30])
    assert c.erg_released
    c.set_spiral_guard(None)
    await _ride(c, [30], t)
    assert not c.erg_released and _last(trainer) == ("erg", 250)


async def test_leaving_erg_clears_the_release(controller, trainer):
    c = controller
    t = await _ride(c, [30, 30, 30, 30])
    assert c.erg_released
    c.set_mode(Mode.RESISTANCE)
    t = await _ride(c, [30], t)
    assert not c.erg_released and _last(trainer)[0] == "resistance"
    c.set_mode(Mode.ERG)  # back in ERG: a fresh 3 s are needed before releasing again
    await _ride(c, [30, 30], t)
    assert not c.erg_released and _last(trainer) == ("erg", 250)


async def test_power_matching_restarts_clean_after_a_release(controller):
    c = controller
    c.use_pedals = True
    c.matcher.integral = 40.0  # built up while grinding under target
    c.matcher.ratio = 1.05
    t = await _ride(c, [30, 30, 30, 30])
    assert c.erg_released
    await _ride(c, [80, 80, 80], t)
    assert not c.erg_released
    assert c.matcher.integral == 0.0
    assert c.matcher.ratio == 1.05  # the learned calibration is kept
