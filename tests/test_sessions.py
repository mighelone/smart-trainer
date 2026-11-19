"""SessionStore: cached session listing and FIT export off the event loop."""

import threading
from collections import deque

import fitdecode

from smart_trainer.config import Settings
from smart_trainer.core import sessions as sessions_mod
from smart_trainer.core.recorder import Recorder
from smart_trainer.core.sessions import SessionStore
from smart_trainer.core.state import Snapshot
from smart_trainer.sync.service import SyncService


def _store(tmp_path) -> SessionStore:
    st = Settings(ftp=200, auto_upload=False)
    st._readonly = True
    sync = SyncService(st, deque(), lambda: [], ride_active=lambda: False)
    return SessionStore(tmp_path, sync, deque())


def _session(store: SessionStore, name: str, now: float, n: int = 3) -> Recorder:
    rec = Recorder.create(store.directory, name, 200, None, now=now)
    for i in range(n):
        rec.record(Snapshot(power=200, cadence=90), i, None, False, "erg", now=now + i)
    return rec


def _count_reads(monkeypatch) -> list:
    reads = []
    real = sessions_mod.read_info

    def counting(path, con=None):
        reads.append(path)
        return real(path, con)

    monkeypatch.setattr(sessions_mod, "read_info", counting)
    return reads


def test_listing_reads_only_changed_sessions(tmp_path, monkeypatch):
    store = _store(tmp_path)
    old = _session(store, "Old", 1_700_000_000)
    old.close()
    new = _session(store, "New", 1_700_100_000)
    reads = _count_reads(monkeypatch)

    assert [s.name for s in store.list_sessions()] == ["New", "Old"]  # most recent first
    assert len(reads) == 2
    assert [s.name for s in store.list_sessions()] == ["New", "Old"]
    assert len(reads) == 2  # nothing changed: served from the cache

    new.record(Snapshot(power=210, cadence=90), 3, None, False, "erg", now=1_700_100_003)
    [first, _] = store.list_sessions()
    assert reads[2:] == [new.path] and first.n_samples == 4  # only the written one re-read
    new.close()


def test_listing_drops_discarded_sessions(tmp_path):
    store = _store(tmp_path)
    for i, name in enumerate(("A", "B")):
        _session(store, name, 1_700_000_000 + i * 100).close()
    [b, a] = store.list_sessions()
    store.discard(b)
    assert [s.name for s in store.list_sessions()] == ["A"]
    assert b.path not in store._cache


def test_listing_limit_reads_only_the_most_recent(tmp_path, monkeypatch):
    store = _store(tmp_path)
    for i in range(5):
        _session(store, f"R{i}", 1_700_000_000 + i * 100).close()
    reads = _count_reads(monkeypatch)
    assert [s.name for s in store.list_sessions(limit=2)] == ["R4", "R3"]
    assert len(reads) == 2
    assert len(store.list_sessions()) == 5
    assert len(reads) == 5  # the first two came from the cache


def test_unfinished_uses_the_listing(tmp_path):
    store = _store(tmp_path)
    active = _session(store, "Crashed", 1_700_000_000)
    done = _session(store, "Done", 1_700_000_100)
    done.set_status("finished")
    assert [s.name for s in store.unfinished()] == ["Crashed"]
    assert store.unfinished(exclude=active.path) == []
    active.set_status("finished")  # the cached status must not go stale
    assert store.unfinished() == []
    active.close()
    done.close()


async def test_finalize_exports_fit_in_a_worker_thread(tmp_path, monkeypatch):
    store = _store(tmp_path)
    rec = _session(store, "Ride", 1_700_000_000, n=10)
    threads = []
    real = sessions_mod._export_fit

    def spy(path):
        threads.append(threading.current_thread())
        return real(path)

    monkeypatch.setattr(sessions_mod, "_export_fit", spy)
    fit = await store.finalize(rec, upload=False)
    rec.close()
    assert threads and threads[0] is not threading.main_thread()
    assert fit is not None and fit.exists()
    with fitdecode.FitReader(fit) as fr:
        records = [f for f in fr if isinstance(f, fitdecode.FitDataMessage) and f.name == "record"]
    assert len(records) == 10
