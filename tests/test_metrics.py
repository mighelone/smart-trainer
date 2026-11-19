"""Training metrics over 1 Hz power samples."""

import random

import pytest

from smart_trainer.core.metrics import (
    intensity_factor,
    normalized_power,
    training_stress_score,
    work_j,
)


def _np_brute_force(powers: list[float]) -> float:
    """The textbook definition, written the slow way."""
    rolling = [sum(powers[i - 29 : i + 1]) / 30 for i in range(29, len(powers))]
    return (sum(r**4 for r in rolling) / len(rolling)) ** 0.25


def test_constant_power_np_is_that_power():
    assert normalized_power([200.0] * 600) == pytest.approx(200)


def test_np_of_alternating_blocks():
    powers = ([100.0] * 300 + [300.0] * 300) * 3
    np_w = normalized_power(powers)
    assert np_w is not None
    assert np_w == pytest.approx(_np_brute_force(powers))
    assert 245 < np_w < 255  # ~ ((100^4 + 300^4) / 2)^(1/4) = 253 W, a bit less from the ramps
    assert np_w > sum(powers) / len(powers)  # NP rewards the variability: avg is 200 W


def test_np_matches_brute_force_on_noisy_power():
    rng = random.Random(42)
    powers = [max(0.0, rng.gauss(220, 60)) for _ in range(1800)]
    assert normalized_power(powers) == pytest.approx(_np_brute_force(powers))


def test_np_needs_30_readings_and_skips_missing_ones():
    assert normalized_power([250.0] * 29) is None
    assert normalized_power([250.0] * 29 + [None] * 10) is None
    assert normalized_power([250.0] * 15 + [None] * 5 + [250.0] * 15) == pytest.approx(250)


def test_one_hour_at_ftp():
    np_w = normalized_power([250.0] * 3600)
    assert np_w is not None
    assert intensity_factor(np_w, 250) == pytest.approx(1.0)
    assert training_stress_score(np_w, 250, 3600) == pytest.approx(100)


def test_tss_scales_with_time_and_intensity_squared():
    assert training_stress_score(200, 250, 1800) == pytest.approx(0.5 * 0.8**2 * 100)


def test_work():
    assert work_j([200.0] * 3600) == 720_000  # 720 kJ
    assert work_j([200.0, None, 100.0]) == 300
