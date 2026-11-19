import base64
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from smart_trainer.sync.base import RideUpload
from smart_trainer.sync.intervals import IntervalsClient, IntervalsError

DOC = json.loads((Path(__file__).parent / "data" / "cadence_endurance_doc.json").read_text())
ZWO = (
    "<workout_file><name>x</name><workout>"
    '<SteadyState Duration="600" Power="0.6"/>'
    "</workout></workout_file>"
)


def handler(request: httpx.Request) -> httpx.Response:
    auth = base64.b64decode(request.headers["authorization"].split()[1]).decode()
    if auth != "API_KEY:secret":
        return httpx.Response(401)
    if request.method == "GET" and request.url.path == "/api/v1/athlete/0/events":
        assert request.url.params["category"] == "WORKOUT"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "name": "Cadence",
                    "type": "Ride",
                    "start_date_local": "2026-11-17T09:00:00",
                    "workout_doc": DOC,
                },
                {
                    "id": 2,
                    "name": "Strength",
                    "type": "WeightTraining",
                    "start_date_local": "2026-11-16T09:00:00",
                },
                {
                    "id": 3,
                    "name": "No doc",
                    "type": "Ride",
                    "start_date_local": "2026-11-18T09:00:00",
                },
            ],
        )
    if request.url.path == "/api/v1/athlete/0/events/3/download.zwo":
        return httpx.Response(200, text=ZWO)
    if request.method == "POST" and request.url.path == "/api/v1/athlete/0/activities":
        assert b'filename="ride.fit"' in request.content
        assert request.url.params["paired_event_id"] == "1"
        return httpx.Response(201, json={"id": "i123"})
    return httpx.Response(404)


async def test_list_and_upload(tmp_path):
    c = IntervalsClient("secret", transport=httpx.MockTransport(handler))
    ws = await c.list_workouts(date(2026, 11, 16), date(2026, 11, 18), ftp=250)
    assert [w.name for w in ws] == ["Cadence", "No doc"]
    assert ws[0].workout.source_id == "1" and ws[1].duration_s == 600
    fit = tmp_path / "ride.fit"
    fit.write_bytes(b"FIT")
    ride = RideUpload(fit, "Ride", workout_source_id="1")
    assert await c.upload_activity(ride) == "i123"
    await c.aclose()


async def test_bad_key():
    c = IntervalsClient("wrong", transport=httpx.MockTransport(handler))
    with pytest.raises(IntervalsError):
        await c.list_workouts(date(2026, 11, 16), date(2026, 11, 18), ftp=250)
    await c.aclose()


# ------------------------------------------------- API key storage and migration
class FakeKeyring:
    """In-memory stand-in for the keyring module functions used by config."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.store[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        from keyring.errors import PasswordDeleteError

        if (service, account) not in self.store:
            raise PasswordDeleteError("not found")
        del self.store[(service, account)]


LEGACY = ("smart-trainer", "intervals.icu")
NEW = ("smart-trainer", "intervals.api_key")


@pytest.fixture
def kr(monkeypatch):
    import keyring

    fake = FakeKeyring()
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, getattr(fake, name))
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    monkeypatch.delenv("SMART_TRAINER_INTERVALS_API_KEY", raising=False)
    return fake


@pytest.fixture
def conn():
    from smart_trainer.config import Settings
    from smart_trainer.sync.intervals import IntervalsConnection

    return IntervalsConnection(Settings())


def test_api_key_migrates_legacy_keyring_entry(kr, conn):
    kr.store[LEGACY] = "old-key"
    assert conn.api_key == "old-key"
    assert kr.store == {NEW: "old-key"}  # copied to the new entry, legacy entry removed
    assert conn.api_key == "old-key"  # now read from the new entry


def test_api_key_new_entry_wins_and_keeps_legacy(kr, conn):
    kr.store[NEW], kr.store[LEGACY] = "new-key", "old-key"
    assert conn.api_key == "new-key"
    assert kr.store[LEGACY] == "old-key"  # untouched: no migration needed


def test_api_key_missing(kr, conn):
    assert conn.api_key is None
    assert not conn.is_configured()
    with pytest.raises(IntervalsError):
        conn.client()


def test_api_key_env_precedence(kr, conn, monkeypatch):
    kr.store[NEW] = "stored"
    monkeypatch.setenv("INTERVALS_API_KEY", "legacy-env")
    assert conn.api_key == "legacy-env"  # env overrides the keyring
    monkeypatch.setenv("SMART_TRAINER_INTERVALS_API_KEY", "new-env")
    assert conn.api_key == "new-env"  # the current variable wins over the deprecated one


def test_set_api_key_writes_new_entry(kr, conn):
    conn.set_api_key("typed")
    assert kr.store == {NEW: "typed"}
    assert conn.is_configured()
