"""SessionStore uploads through injected connections (no network)."""

from smart_trainer.config import Settings
from smart_trainer.core.engine import Engine
from smart_trainer.core.recorder import Recorder, read_info
from smart_trainer.core.sessions import SessionStore
from smart_trainer.core.state import Snapshot
from smart_trainer.sync.base import AthleteProfile, Capability, Connection, RideUpload
from smart_trainer.sync.service import SyncService


class FakeConnection(Connection):
    """An upload connection that records uploads, or fails them on demand."""

    id = "good"
    name = "Good"
    capabilities = frozenset({Capability.UPLOAD, Capability.PROFILE})
    configured = True

    def __init__(self, settings, fail=False):
        super().__init__(settings)
        self.fail = fail
        self.uploads: list[RideUpload] = []

    def is_configured(self) -> bool:
        return self.configured

    async def test(self) -> str:
        return "me"

    async def upload_activity(self, ride: RideUpload) -> str:
        if self.fail:
            raise RuntimeError("server down")
        self.uploads.append(ride)
        return f"{self.id}-1"

    async def profile(self) -> AthleteProfile:
        return AthleteProfile(ftp=250)


class FlakyConnection(FakeConnection):
    id = "flaky"
    name = "Flaky"


class UnconfiguredConnection(FakeConnection):
    id = "nokey"
    name = "No key"
    configured = False


def _store(tmp_path, conns, readonly=False, **settings):
    st = Settings(ftp=200, **settings)
    st.readonly = readonly
    for conn in conns:
        conn.settings = st
    sync = SyncService(st, lambda: list(conns), ride_active=lambda: False)
    return SessionStore(tmp_path, sync)


def _recorded(store: SessionStore, name="Test ride", now=1_700_000_000) -> Recorder:
    # explicit start time: session files are named after the second they start in
    rec = Recorder.create(store.directory, name, 200, None, now=now)
    for index in range(5):
        rec.record(
            Snapshot(power=200, cadence=90, hr=140),
            index,
            None,
            False,
            "resistance",
            now=now + index,
        )
    return rec


def _conns():
    st = Settings()
    return FakeConnection(st), FlakyConnection(st, fail=True), UnconfiguredConnection(st)


async def test_finalize_uploads_to_every_connection(tmp_path):
    good, flaky, nokey = _conns()
    store = _store(tmp_path, [good, flaky, nokey])
    rec = _recorded(store)
    result = await store.finalize(rec)
    info = read_info(rec.path)
    rec.close()
    fit = result.fit
    assert fit and fit.exists()
    assert [u.fit_path for u in good.uploads] == [fit] and good.uploads[0].name == "Test ride"
    assert info.status == "finished"
    assert info.uploaded("good") and info.uploads["flaky"].error == "server down"
    assert "nokey" not in info.uploads  # not configured: never tried
    assert result.upload is not None and not result.upload.ok
    assert result.upload.messages == [
        "Uploaded to Good (good-1)",
        "Flaky upload failed: server down",
    ]


async def test_retry_skips_connections_already_done(tmp_path):
    good, flaky, _ = _conns()
    store = _store(tmp_path, [good, flaky])
    rec = _recorded(store)
    await store.finalize(rec)
    rec.close()
    flaky.fail = False
    report = await store.upload_session(read_info(rec.path))
    assert report.ok and report.messages == ["Uploaded to Flaky (flaky-1)"]
    assert len(good.uploads) == 1  # not uploaded twice
    info = read_info(rec.path)
    assert info.uploaded("good") and info.uploaded("flaky")
    report = await store.upload_session(info)  # nothing left to do
    assert not report.results and report.skipped


async def test_auto_upload_targets(tmp_path):
    good, flaky, _ = _conns()
    store = _store(tmp_path, [good, flaky])
    good.settings.connection("flaky").auto_upload = False
    assert store._sync.auto_upload_targets() == ["good"]
    rec = _recorded(store)
    await store.finalize(rec)  # automatic: only "good"
    assert set(read_info(rec.path).uploads) == {"good"}
    rec.close()

    good.settings.auto_upload = False
    assert store._sync.auto_upload_targets() == []
    rec = _recorded(store, "Ride 2", now=1_700_100_000)
    await store.finalize(rec)
    assert read_info(rec.path).uploads == {}
    await store.finalize(rec, upload=True)  # explicit: every connected service
    assert set(read_info(rec.path).uploads) == {"good", "flaky"}
    rec.close()


async def test_disabled_connection_is_skipped(tmp_path):
    good, flaky, _ = _conns()
    store = _store(tmp_path, [good, flaky])
    good.settings.connection("flaky").enabled = False
    rec = _recorded(store)
    await store.finalize(rec)
    assert set(read_info(rec.path).uploads) == {"good"}
    rec.close()


async def test_no_connection_leaves_session_finished(tmp_path):
    store = _store(tmp_path, [])
    rec = _recorded(store)
    result = await store.finalize(rec, upload=True)
    info = read_info(rec.path)
    assert info.status == "finished" and info.uploads == {}
    assert result.upload and result.upload.messages == [
        "No connection to upload to: saved locally only"
    ]
    rec.close()
    assert store.unfinished() == []


async def test_demo_mode_never_uploads(tmp_path):
    good, _, _ = _conns()
    store = _store(tmp_path, [good], readonly=True)
    rec = _recorded(store)
    result = await store.finalize(rec)
    rec.close()
    assert good.uploads == []
    assert result.upload and result.upload.skipped == "Demo mode: uploads are disabled"
    report = await store.upload_session(read_info(rec.path))
    assert not report.results and not report.ok
    assert read_info(rec.path).status == "finished"


async def test_engine_without_connections_has_no_sync(tmp_path):
    engine = Engine.from_settings(Settings(), sessions=tmp_path, run_loop=False)
    assert engine.sync.connections() == []
    result = await engine.sync.sync_profile()
    assert result.skipped == "No profile connection configured: profile not synced"
