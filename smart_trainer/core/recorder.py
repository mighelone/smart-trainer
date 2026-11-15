"""Crash-safe session recording: one SQLite file per session, committed every second."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from smart_trainer.core.state import Snapshot
from smart_trainer.core.workout import Workout

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL,              -- active | finished | uploaded
    started_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    name TEXT NOT NULL,
    ftp INTEGER NOT NULL,
    workout_json TEXT,
    elapsed_s REAL NOT NULL DEFAULT 0, -- active workout seconds (for resume)
    mode TEXT,
    upload_id TEXT,
    upload_error TEXT
);
CREATE TABLE IF NOT EXISTS samples (
    t REAL PRIMARY KEY,                -- unix time (s)
    elapsed REAL,
    lap INTEGER NOT NULL DEFAULT 0,
    paused INTEGER NOT NULL DEFAULT 0,
    power REAL, trainer_power REAL, pedal_power REAL,
    cadence REAL, hr INTEGER, speed_kmh REAL, distance_m REAL,
    target REAL, balance_left REAL
);
"""


@dataclass
class SessionInfo:
    path: Path
    status: str
    name: str
    started_at: float
    updated_at: float
    ftp: int
    elapsed_s: float
    workout: Workout | None
    upload_id: str | None
    upload_error: str | None
    n_samples: int

    @property
    def started(self) -> datetime:
        return datetime.fromtimestamp(self.started_at)


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, isolation_level=None)  # autocommit; we wrap writes explicitly
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.row_factory = sqlite3.Row
    return con


class Recorder:
    def __init__(self, path: Path, con: sqlite3.Connection) -> None:
        self.path = path
        self.con = con
        self.distance_m = 0.0
        self.lap = 0
        self._last_t: float | None = None
        row = con.execute(
            "SELECT MAX(lap) AS lap, MAX(distance_m) AS d, MAX(t) AS t FROM samples"
        ).fetchone()
        if row and row["t"] is not None:
            self.lap, self.distance_m, self._last_t = row["lap"] or 0, row["d"] or 0.0, row["t"]

    # -------------------------------------------------------------- lifecycle
    @classmethod
    def create(
        cls, directory: Path, name: str, ftp: int, workout: Workout | None, now: float | None = None
    ) -> Recorder:
        now = now or time.time()
        stamp = datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S")
        path = directory / f"session-{stamp}.sqlite"
        con = _connect(path)
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO session (id, status, started_at, updated_at, name, ftp, workout_json) "
            "VALUES (1, 'active', ?, ?, ?, ?, ?)",
            (now, now, name, ftp, json.dumps(workout.to_dict()) if workout else None),
        )
        return cls(path, con)

    @classmethod
    def open(cls, path: Path) -> Recorder:
        return cls(path, _connect(path))

    def record(
        self,
        snap: Snapshot,
        elapsed: float,
        target: float | None,
        paused: bool,
        mode: str,
        now: float | None = None,
    ) -> None:
        now = now or time.time()
        if self._last_t is not None and not paused and snap.speed_kmh:
            dt = min(now - self._last_t, 5.0)  # don't invent distance across crash gaps
            self.distance_m += snap.speed_kmh / 3.6 * dt
        self._last_t = now
        with self.con:  # one transaction per second
            self.con.execute("BEGIN")
            self.con.execute(
                "INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    now,
                    elapsed,
                    self.lap,
                    int(paused),
                    snap.power,
                    snap.trainer_power,
                    snap.pedal_power,
                    snap.cadence,
                    snap.hr,
                    snap.speed_kmh,
                    self.distance_m,
                    target,
                    snap.balance_left,
                ),
            )
            self.con.execute(
                "UPDATE session SET updated_at=?, elapsed_s=?, mode=? WHERE id=1",
                (now, elapsed, mode),
            )

    def new_lap(self) -> None:
        self.lap += 1

    def set_status(self, status: str, **fields) -> None:
        sets = ", ".join(["status=?"] + [f"{k}=?" for k in fields])
        self.con.execute(f"UPDATE session SET {sets} WHERE id=1", (status, *fields.values()))

    def close(self) -> None:
        self.con.close()

    def info(self) -> SessionInfo:
        return read_info(self.path, self.con)

    def samples(self) -> list[sqlite3.Row]:
        return self.con.execute("SELECT * FROM samples ORDER BY t").fetchall()


def read_info(path: Path, con: sqlite3.Connection | None = None) -> SessionInfo:
    own = con is None
    con = con or _connect(path)
    try:
        r = con.execute("SELECT * FROM session WHERE id=1").fetchone()
        n = con.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        wo = Workout.from_dict(json.loads(r["workout_json"])) if r["workout_json"] else None
        return SessionInfo(
            path,
            r["status"],
            r["name"],
            r["started_at"],
            r["updated_at"],
            r["ftp"],
            r["elapsed_s"],
            wo,
            r["upload_id"],
            r["upload_error"],
            n,
        )
    finally:
        if own:
            con.close()


def list_sessions(directory: Path) -> list[SessionInfo]:
    out = []
    for p in sorted(directory.glob("session-*.sqlite"), reverse=True):
        try:
            out.append(read_info(p))
        except Exception:
            continue
    return out


def find_unfinished(directory: Path) -> list[SessionInfo]:
    """Sessions left `active` (app crashed or was killed while riding)."""
    return [s for s in list_sessions(directory) if s.status == "active"]
