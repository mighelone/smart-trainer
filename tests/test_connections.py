import json
import sqlite3

import pytest

import smart_trainer.sync.base as base
from smart_trainer.config import Settings
from smart_trainer.core.engine import Engine
from smart_trainer.core.recorder import Recorder, read_info
from smart_trainer.core.state import Snapshot
from smart_trainer.sync import registry
from smart_trainer.sync.base import Capability, Connection, RideUpload


class FakeUploader(Connection):
    id = "good"
    name = "Good"
    capabilities = frozenset({Capability.UPLOAD})
    uploads: list[RideUpload] = []

    def is_configured(self) -> bool:
        return True

    async def test(self) -> str:
        return "me"

    async def upload_activity(self, ride: RideUpload) -> str:
        self.uploads.append(ride)
        return f"{self.id}-1"


class FlakyUploader(FakeUploader):
    id = "flaky"
    name = "Flaky"
    fail = True

    async def upload_activity(self, ride: RideUpload) -> str:
        if FlakyUploader.fail:
            raise RuntimeError("server down")
        return await super().upload_activity(ride)


class Unconfigured(FakeUploader):
    id = "nokey"
    name = "No key"

    def is_configured(self) -> bool:
        return False


@pytest.fixture
def fake_registry(monkeypatch):
    reg = {}
    monkeypatch.setattr(base, "REGISTRY", reg)
    monkeypatch.setattr(registry, "REGISTRY", reg)
    for cls in (FakeUploader, FlakyUploader, Unconfigured):
        base.register(cls)
    FakeUploader.uploads = []
    FlakyUploader.fail = True
    return reg


def test_builtin_connections_registered():
    st = Settings()
    ids = [c.id for c in registry.all_connections(st)]
    assert ids[:2] == ["intervals", "strava"]
    assert [c.id for c in registry.with_capability(st, Capability.PROFILE)] == ["intervals"]
    assert {"intervals", "strava"} <= {
        c.id for c in registry.with_capability(st, Capability.UPLOAD)
    }
    strava = registry.get(st, "strava")
    assert strava is not None and strava.name == "Strava"
    assert registry.get(st, "nope") is None


def test_connected_filters_enabled_and_configured(fake_registry):
    st = Settings()
    st.connection("flaky").enabled = False
    assert [c.id for c in registry.connected(st, Capability.UPLOAD)] == ["good"]
    assert registry.connected(st, Capability.PROFILE) == []


def test_settings_migrates_flat_intervals_fields():
    st = Settings.model_validate_json(
        json.dumps({"ftp": 230, "intervals_athlete_id": "i42", "auto_upload": False})
    )
    assert st.connections["intervals"].options["athlete_id"] == "i42"
    assert st.auto_upload is False
    assert "intervals_athlete_id" not in st.model_dump()
    assert Settings.model_validate(st.model_dump()) == st  # round-trips


def _session(tmp_path) -> Recorder:
    rec = Recorder.create(tmp_path, "Ride", 250, None, now=1_700_000_000)
    for i in range(5):
        rec.record(Snapshot(power=200, cadence=90), i, None, False, "erg", now=1_700_000_000 + i)
    return rec


def test_recorder_upload_state(tmp_path):
    rec = _session(tmp_path)
    rec.set_upload("strava", error="boom")
    rec.set_upload("intervals", "i1")
    info = rec.info()
    assert info.uploaded("intervals") and not info.uploaded("strava")
    assert info.uploads["strava"].error == "boom"
    rec.set_upload("strava", "s1")
    assert rec.info().uploaded("strava")
    rec.close()


def test_legacy_session_reads_intervals_upload(tmp_path):
    rec = _session(tmp_path)
    path = rec.path
    rec.close()
    con = sqlite3.connect(path)  # a session file from before the uploads table
    con.execute("DROP TABLE uploads")
    con.execute("UPDATE session SET status='uploaded', upload_id='i9' WHERE id=1")
    con.commit()
    con.close()
    info = read_info(path)
    assert info.status == "finished" and info.uploaded("intervals")
    rec = Recorder.open(path)
    rec.set_upload("strava", "s1")  # creates the table on demand
    info = rec.info()
    assert info.uploaded("strava") and info.uploaded("intervals")
    rec.close()


async def test_engine_uploads_everywhere_and_retries_failures(tmp_path, fake_registry):
    e = Engine(Settings(), sessions=tmp_path)
    rec = _session(tmp_path)
    fit = tmp_path / "ride.fit"
    fit.write_bytes(b"FIT")
    assert await e.upload(rec, fit) == {"good": True, "flaky": False}
    info = rec.info()
    assert info.uploaded("good") and info.uploads["flaky"].error == "server down"
    assert "Flaky upload failed: server down" in e.messages
    FlakyUploader.fail = False
    assert await e.upload(rec, fit) == {"flaky": True}  # "good" is not uploaded twice
    assert len(FakeUploader.uploads) == 2
    assert await e.upload(rec, fit) == {}
    assert "saved locally" in e.messages[-1]
    rec.close()


async def test_finalize_auto_upload_targets(tmp_path, fake_registry):
    st = Settings()
    st.connection("flaky").auto_upload = False
    e = Engine(st, sessions=tmp_path)
    assert e.auto_upload_targets() == ["good"]
    rec = _session(tmp_path)
    await e.finalize(rec)  # auto: only "good"
    info = rec.info()
    assert info.uploaded("good") and "flaky" not in info.uploads
    rec.close()

    st.auto_upload = False
    assert e.auto_upload_targets() == []
    rec = Recorder.create(tmp_path, "Ride 2", 250, None, now=1_700_100_000)
    rec.record(Snapshot(power=200), 0, None, False, "erg", now=1_700_100_000)
    await e.finalize(rec, upload=True)  # explicit: every connected target
    assert set(rec.info().uploads) == {"good", "flaky"}
    rec.close()


async def test_demo_mode_never_uploads(tmp_path, fake_registry):
    st = Settings()
    st._readonly = True
    e = Engine(st, sessions=tmp_path)
    rec = _session(tmp_path)
    assert await e.upload(rec, tmp_path / "x.fit") == {}
    assert FakeUploader.uploads == []
    rec.close()
