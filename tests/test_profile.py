import httpx
import pytest

import smart_trainer.sync.intervals as intervals_mod
from smart_trainer.config import DEFAULT_POWER_ZONES, Settings
from smart_trainer.core.engine import Engine, RideState
from smart_trainer.core.workout import parse_intervals_doc, zone_mid
from smart_trainer.sync.base import AthleteProfile
from smart_trainer.sync.intervals import IntervalsClient, parse_profile
from smart_trainer.ui.widgets import hr_zone_color, zone_color

RUN = {"types": ["Run"], "ftp": None, "lthr": 170, "max_hr": 195, "hr_zones": [1, 2, 3]}
RIDE = {
    "types": ["Ride", "VirtualRide"],
    "ftp": 280,
    "indoor_ftp": 265,
    "power_zones": [56, 76, 91, 106, 121, 151, 999],
    "power_zone_names": ["Recovery", "Endurance", "Tempo", "Threshold", "VO2", "Anaerobic", "NM"],
    "lthr": 165,
    "max_hr": 190,
    "hr_zones": [135, 150, 160, 168, 175, 182, 190],
}
ATHLETE = {"id": "i1", "name": "Rider", "icu_weight": 71.5, "sportSettings": [RUN, RIDE]}


def test_parse_profile_picks_ride_and_indoor_ftp():
    p = parse_profile(ATHLETE)
    assert p.ftp == 265  # indoor FTP preferred
    assert p.weight_kg == 71.5
    assert p.power_zones == RIDE["power_zones"]
    assert p.power_zone_names[0] == "Recovery"
    assert p.hr_zones == RIDE["hr_zones"]
    assert (p.lthr, p.max_hr) == (165, 190)  # from Ride, not Run


def test_parse_profile_falls_back_to_ftp_and_handles_missing():
    p = parse_profile({"sportSettings": [{**RIDE, "indoor_ftp": None}]})
    assert p.ftp == 280
    assert p.weight_kg is None
    empty = parse_profile({})
    assert empty == AthleteProfile()


def test_apply_only_overwrites_known_values():
    s = Settings(ftp=200, weight_kg=80.0, lthr=150)
    changed = AthleteProfile(ftp=265, power_zones=[50, 100, 999]).apply(s)
    assert changed == ["FTP", "power zones"]
    assert (s.ftp, s.weight_kg, s.lthr, s.power_zones) == (265, 80.0, 150, [50, 100, 999])
    assert AthleteProfile(ftp=265).apply(s) == []


def test_zone_targets_use_rider_zones():
    doc = {"steps": [{"duration": 60, "power": {"units": "power_zone", "value": 3}}]}
    assert parse_intervals_doc(doc, ftp=200).steps[0].start == 0.82  # Coggan default
    custom = parse_intervals_doc(doc, ftp=200, zones=[50, 70, 80, 100, 999])
    assert custom.steps[0].start == pytest.approx(0.75)
    assert zone_mid(1, [50, 70, 999]) == pytest.approx(0.45)
    assert zone_mid(3, [50, 70, 999]) == pytest.approx(0.80)  # open-ended top zone
    assert zone_mid(9, [50, 70, 999]) == pytest.approx(0.80)  # clamped


def test_zone_colors():
    assert zone_color(None, 200) == zone_color(100, 0)  # unknown -> grey
    assert zone_color(100, 200) != zone_color(250, 200)
    assert zone_color(100, 200, [40, 999]) == zone_color(400, 200, [40, 999])
    assert zone_color(100, 200) == zone_color(100, 200, list(DEFAULT_POWER_ZONES))
    assert hr_zone_color(150, []) is None
    assert hr_zone_color(None, [140]) is None
    assert hr_zone_color(130, [140, 160]) != hr_zone_color(150, [140, 160])


# ------------------------------------------------------------- engine sync
def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/athlete/0":
        return httpx.Response(200, json=ATHLETE)
    return httpx.Response(404)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "path", classmethod(lambda cls: tmp_path / "config.json"))
    monkeypatch.setattr(intervals_mod, "get_api_key", lambda: "secret")
    real = IntervalsClient
    monkeypatch.setattr(
        intervals_mod,
        "IntervalsClient",
        lambda key, athlete: real(key, athlete, transport=httpx.MockTransport(_handler)),
    )
    return Engine(Settings(ftp=200, auto_upload=False), sessions=tmp_path)


async def test_engine_sync_profile_updates_and_saves(engine, tmp_path):
    changed = await engine.sync_profile()
    assert "FTP" in changed and "weight" in changed
    assert engine.settings.ftp == 265
    assert engine.settings.profile_synced_at is not None
    assert Settings.load().ftp == 265  # persisted
    assert await engine.sync_profile() == []  # nothing new


async def test_engine_sync_profile_skipped(engine, monkeypatch):
    engine.state = RideState.RIDING
    assert await engine.sync_profile() is None
    assert engine.settings.ftp == 200 and "ride" in engine.messages[-1]
    engine.state = RideState.IDLE
    monkeypatch.setattr(intervals_mod, "get_api_key", lambda: None)
    assert await engine.sync_profile() is None
    engine.settings._readonly = True
    assert await engine.sync_profile() is None
    assert engine.settings.ftp == 200


async def test_engine_sync_profile_error(engine, monkeypatch):
    real = IntervalsClient
    monkeypatch.setattr(
        intervals_mod,
        "IntervalsClient",
        lambda key, athlete: real(
            key, athlete, transport=httpx.MockTransport(lambda r: httpx.Response(401))
        ),
    )
    assert await engine.sync_profile() is None
    assert "rejected" in engine.messages[-1]
    assert engine.settings.profile_synced_at is None
