import json
import sqlite3

from smart_trainer.config import Settings
from smart_trainer.core.recorder import Recorder, read_info
from smart_trainer.core.state import Snapshot
from smart_trainer.sync import registry
from smart_trainer.sync.base import Capability


def test_builtin_connections_registered():
    st = Settings()
    conns = registry.all_connections(st)
    assert [c.id for c in conns][:2] == ["intervals", "strava"]
    caps = {c.id: c.capabilities for c in conns}
    assert caps["intervals"] == {Capability.UPLOAD, Capability.WORKOUTS, Capability.PROFILE}
    assert caps["strava"] == {Capability.UPLOAD}
    strava = registry.get(st, "strava")
    assert strava is not None and strava.name == "Strava"
    assert registry.get(st, "nope") is None


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
