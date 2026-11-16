from smart_trainer.config import PowerMatch
from smart_trainer.core.control import PowerMatcher
from smart_trainer.core.state import Snapshot


def simulate(bias: float, target: float, seconds: int = 240, matcher=None):
    """Trainer reads `bias` x true power and drives its own reading to the command."""
    m = matcher or PowerMatcher(PowerMatch())
    true_power, cmd = target, target
    for t in range(seconds):
        true_power += (cmd / bias - true_power) * 0.5
        snap = Snapshot(power=true_power, pedal_power=true_power, cadence=90)
        cmd = m.command(
            target, snap, pedal_avg=true_power, trainer_avg=true_power * bias, now=float(t)
        )
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
    cmd = m.command(200, Snapshot(cadence=90, pedal_power=150), 150, 190, now=1000.0)
    assert cmd <= 210


def test_no_learning_when_not_pedaling():
    m = PowerMatcher(PowerMatch())
    for t in range(60):
        m.command(200, Snapshot(cadence=0, pedal_power=0), 0, 0, now=float(t))
    assert m.ratio == 1.0 and m.integral == 0.0
