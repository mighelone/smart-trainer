"""Source selection and role assignment, tested without devices or an engine."""

from dataclasses import dataclass, field

from smart_trainer.config import PairedDevice
from smart_trainer.core.devices import DeviceHub
from smart_trainer.core.sources import SourceSelector
from smart_trainer.core.state import Assignment, Reading, Role, Snapshot

PEDALS, TRAINER, HRM = "pedals", "trainer", "hrm"
BOTH = Assignment({Role.TRAINER: TRAINER, Role.POWER: PEDALS, Role.HR: HRM})


@dataclass
class FakeConfig:
    """A DeviceConfig that counts saves instead of writing a file."""

    role_assignment: dict[str, str] = field(default_factory=dict)
    power_source: str = "pedals"
    devices: dict[str, PairedDevice] = field(default_factory=dict)
    saves: int = 0

    def save(self) -> None:
        self.saves += 1


def _assignment(role_assignment: dict[str, str], power_source: str) -> Assignment:
    config = FakeConfig(role_assignment, power_source)
    return DeviceHub(config, on_reading=lambda r: None).assignment


def _selector(now: float = 100.0) -> SourceSelector:
    sel = SourceSelector()
    for dt in (2.0, 1.0, 0.0):  # three samples within the 3 s window
        sel.on_reading(Reading(PEDALS, power=200 + dt * 10, cadence=90, ts=now - dt))
        sel.on_reading(Reading(TRAINER, power=210, speed_kmh=30, ts=now - dt))
    sel.on_reading(Reading(HRM, hr=140, ts=now))
    return sel


def test_assignment_applies_power_source():
    saved = {"trainer": TRAINER, "power": PEDALS}
    assert _assignment(saved, "pedals").power_id == PEDALS
    trainer_power = _assignment(saved, "trainer")
    assert trainer_power.power_id == TRAINER and not trainer_power.has_pedals
    assert _assignment({"power": PEDALS}, "trainer").power_id == PEDALS  # no trainer


def test_snapshot_prefers_pedals_and_averages():
    snap = _selector().create_snapshot(BOTH, now=100.0)
    assert (snap.power, snap.power_source) == (200, PEDALS)
    assert (snap.cadence, snap.speed_kmh, snap.hr) == (90, 30, 140)
    assert snap.pedal_power == 200 and snap.trainer_power == 210
    assert snap.power_avg == snap.pedal_avg == 210  # mean of 220, 210, 200
    assert snap.trainer_avg == 210
    assert snap.warnings == []


def test_snapshot_falls_back_to_trainer_when_pedals_go_stale():
    sel = _selector(now=100.0)
    sel.on_reading(Reading(TRAINER, power=180, ts=104.0))
    snap = sel.create_snapshot(BOTH, now=104.0)  # pedals last seen 4 s ago
    assert (snap.power, snap.power_source) == (180, TRAINER)
    assert snap.pedal_power is None and snap.warnings


def test_trainer_as_power_source_has_no_pedal_values():
    trainer_only = _assignment({"trainer": TRAINER, "power": PEDALS}, "trainer")
    snap = _selector().create_snapshot(trainer_only, now=100.0)
    assert snap.power_source == TRAINER
    assert snap.pedal_power is None and snap.pedal_avg is None


def test_hub_publishes_and_persists_assignment():
    config = FakeConfig({"trainer": TRAINER})
    seen: list[Assignment] = []
    hub = DeviceHub(
        config,
        on_reading=lambda r: None,
        on_change=lambda assignment, trainer: seen.append(assignment),
    )
    assert seen == [hub.assignment] and hub.assignment.trainer_id == TRAINER
    hub.assign(PEDALS, [Role.POWER])
    assert seen[-1].power_id == PEDALS and seen[-1].has_pedals
    hub.set_power_source("trainer")
    assert seen[-1].power_id == TRAINER and not seen[-1].has_pedals
    assert config.role_assignment == {"trainer": TRAINER, "power": PEDALS}
    assert config.power_source == "trainer" and config.saves == 2


# ------------------------------------------------------------- power_mean (recorded)
PEDALS_ONLY = Assignment({Role.POWER: PEDALS})
TRAINER_AND_PEDALS = BOTH


def _readings(sel: SourceSelector, device: str, values: list[float], start: float, hz: float):
    for i, v in enumerate(values):
        sel.on_reading(Reading(device, power=v, cadence=90, ts=start + (i + 1) / hz))


def test_power_mean_at_4_hz_is_the_mean_of_the_second():
    sel = SourceSelector()
    sel.create_snapshot(PEDALS_ONLY, now=99.0)
    _readings(sel, PEDALS, [180, 200, 220, 240], start=99.0, hz=4)  # 99.25 ... 100.0
    snap = sel.create_snapshot(PEDALS_ONLY, now=100.0)
    assert snap.power == 240  # the latest reading
    assert snap.power_mean == 210  # what gets recorded


def test_power_mean_only_counts_readings_since_the_previous_snapshot():
    sel = SourceSelector()
    sel.create_snapshot(PEDALS_ONLY, now=99.0)
    _readings(sel, PEDALS, [100, 300], start=99.0, hz=2)  # 99.5, 100.0
    assert sel.create_snapshot(PEDALS_ONLY, now=100.0).power_mean == 200
    _readings(sel, PEDALS, [400], start=100.0, hz=1)  # 101.0
    assert sel.create_snapshot(PEDALS_ONLY, now=101.0).power_mean == 400


def test_power_mean_at_1_hz_and_without_a_reading_in_the_second():
    sel = SourceSelector()
    sel.create_snapshot(PEDALS_ONLY, now=99.0)
    _readings(sel, PEDALS, [250], start=99.0, hz=1.25)  # 99.8
    assert sel.create_snapshot(PEDALS_ONLY, now=100.0).power_mean == 250
    # no reading in (100, 101]: the latest (not stale yet) stands for the second
    snap = sel.create_snapshot(PEDALS_ONLY, now=101.0)
    assert snap.power_mean == snap.power == 250


def test_power_mean_spans_a_late_tick():
    sel = SourceSelector()
    sel.create_snapshot(PEDALS_ONLY, now=100.0)
    _readings(sel, PEDALS, [100, 200, 300], start=100.0, hz=1)  # 101, 102, 103
    assert sel.create_snapshot(PEDALS_ONLY, now=103.0).power_mean == 200


def test_first_power_mean_uses_the_last_second():
    sel = SourceSelector()
    sel.on_reading(Reading(PEDALS, power=500, ts=98.5))  # older than a second
    sel.on_reading(Reading(PEDALS, power=100, ts=99.5))
    sel.on_reading(Reading(PEDALS, power=200, ts=100.0))
    assert sel.create_snapshot(PEDALS_ONLY, now=100.0).power_mean == 150


def test_power_mean_follows_the_power_source_fallback():
    sel = SourceSelector()
    sel.create_snapshot(TRAINER_AND_PEDALS, now=99.0)
    sel.on_reading(Reading(PEDALS, power=200, ts=95.0))  # stale by now
    _readings(sel, TRAINER, [190, 210], start=99.0, hz=2)
    snap = sel.create_snapshot(TRAINER_AND_PEDALS, now=100.0)
    assert snap.power_source == TRAINER and snap.power_mean == 200


def test_hand_built_snapshot_records_its_power():
    assert Snapshot(power=180).power_mean == 180
    assert Snapshot(power=180, power_mean=175).power_mean == 175
