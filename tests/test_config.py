"""Settings changes: validated, applied in place and saved (never in demo mode)."""

import pytest

from smart_trainer.config import Settings
from smart_trainer.sync.intervals import IntervalsConnection


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    """Settings saved to a temporary config file."""
    monkeypatch.setattr(Settings, "path", classmethod(lambda cls: tmp_path / "config.json"))
    return Settings(ftp=200)


def test_update_applies_and_saves(settings):
    settings.update(ftp=260, interval_cues=False)
    assert settings.ftp == 260 and not settings.interval_cues
    saved = Settings.load()
    assert saved.ftp == 260 and not saved.interval_cues


@pytest.mark.parametrize(
    "changes",
    [
        {"ftp": "lots"},  # wrong type
        {"fpt": 260},  # unknown field
        {"ftp": 260, "power_match": {"settle_s": "soon"}},  # one bad value spoils all
    ],
)
def test_invalid_update_changes_nothing(settings, changes):
    with pytest.raises(ValueError):
        settings.update(**changes)
    assert settings.ftp == 200 and settings.power_match.settle_s == 5.0
    assert not Settings.path().exists()


def test_nested_update_keeps_the_same_object(settings):
    power_match = settings.power_match  # the trainer controller holds this object
    settings.update(power_match={"max_offset_w": 40.0})
    assert settings.power_match is power_match
    assert power_match.max_offset_w == 40.0 and power_match.settle_s == 5.0


def test_readonly_applies_without_saving(settings):
    settings.readonly = True
    settings.update(ftp=280)
    assert settings.ftp == 280 and not Settings.path().exists()


def test_update_connection_merges_options(settings):
    settings.update_connection("strava", options={"client_id": "123"})
    settings.update_connection("strava", enabled=False, options={"athlete": "Ann"})
    conf = Settings.load().connection("strava")
    assert not conf.enabled
    assert conf.options == {"client_id": "123", "athlete": "Ann"}


def test_connection_update_settings(settings):
    conn = IntervalsConnection(settings)
    conn.update_settings(options={"athlete_id": "i42"})
    assert conn.athlete_id == "i42"
    assert Settings.load().connection(conn.id).options["athlete_id"] == "i42"


async def test_intervals_client_can_use_a_typed_athlete_id(settings):
    conn = IntervalsConnection(settings)
    async with conn.client("key", athlete_id="i7") as typed, conn.client("key") as stored:
        assert typed.athlete_id == "i7" and stored.athlete_id == "0"
