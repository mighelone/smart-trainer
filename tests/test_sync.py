"""SessionStore uploads through an injected SyncProvider (no network)."""

from collections import deque
from pathlib import Path

from smart_trainer.config import Settings
from smart_trainer.core.recorder import Recorder, read_info
from smart_trainer.core.sessions import SessionStore
from smart_trainer.core.state import Snapshot
from smart_trainer.sync.base import AthleteProfile
from smart_trainer.sync.service import SyncService


class FakeProvider:
    """A SyncProvider that records uploads, or fails them on demand."""

    name = "fake"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.uploads: list[tuple[str, str]] = []
        self.closed = False

    async def list_workouts(self, oldest, newest, ftp, zones=None):
        return []

    async def profile(self) -> AthleteProfile:
        return AthleteProfile(ftp=250)

    async def upload_activity(
        self, fit_path: Path, name: str, description="", paired_event_id=None
    ):
        if self.fail:
            raise RuntimeError("boom")
        self.uploads.append((fit_path.name, name))
        return "a42"

    async def aclose(self) -> None:
        self.closed = True


def _store(tmp_path, provider, readonly=False):
    settings = Settings(ftp=200, auto_upload=True)
    settings._readonly = readonly
    messages: deque[str] = deque()
    sync = SyncService(settings, messages, lambda: provider, ride_active=lambda: False)
    return SessionStore(tmp_path, settings, sync, messages), messages


def _recorded(store: SessionStore) -> Recorder:
    rec = store.create("Test ride", None)
    for i in range(5):
        rec.record(
            Snapshot(power=200, cadence=90, hr=140),
            i,
            None,
            False,
            "resistance",
            now=1_700_000_000 + i,
        )
    return rec


async def test_finalize_uploads_and_marks_session(tmp_path):
    provider = FakeProvider()
    store, messages = _store(tmp_path, provider)
    rec = _recorded(store)
    fit = await store.finalize(rec)
    info = read_info(rec.path)
    rec.close()
    assert fit and fit.exists()
    assert provider.uploads == [(fit.name, "Test ride")] and provider.closed
    assert (info.status, info.upload_id) == ("uploaded", "a42")
    assert messages[-1] == "Uploaded to fake (a42)"


async def test_upload_failure_is_recorded(tmp_path):
    provider = FakeProvider(fail=True)
    store, messages = _store(tmp_path, provider)
    rec = _recorded(store)
    await store.finalize(rec)
    info = read_info(rec.path)
    rec.close()
    assert (info.status, info.upload_error) == ("finished", "boom")
    assert "Upload failed" in messages[-1] and provider.closed


async def test_no_account_leaves_session_finished(tmp_path):
    store, messages = _store(tmp_path, None)
    rec = _recorded(store)
    await store.finalize(rec)
    assert read_info(rec.path).status == "finished" and "API key" in messages[-1]
    rec.close()
    assert store.unfinished() == []


async def test_demo_mode_never_uploads(tmp_path):
    provider = FakeProvider()
    store, messages = _store(tmp_path, provider, readonly=True)
    rec = _recorded(store)
    await store.finalize(rec)
    rec.close()
    assert provider.uploads == [] and "Demo mode" in messages[-1]
    assert await store.upload_session(read_info(rec.path)) is False
    assert read_info(rec.path).status == "finished"
