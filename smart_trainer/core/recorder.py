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
    """Metadata of a recorded session.

    Attributes:
        path (Path): Path of the session SQLite file.
        status (str): ``"active"``, ``"finished"`` or ``"uploaded"``.
        name (str): Session name.
        started_at (float): Unix time the session started.
        updated_at (float): Unix time of the last recorded sample.
        ftp (int): FTP used for the session, in watts.
        elapsed_s (float): Active workout seconds (for resume).
        workout (Workout | None): Workout being ridden, if any.
        upload_id (str | None): intervals.icu activity id once uploaded.
        upload_error (str | None): Last upload error message, if any.
        n_samples (int): Number of recorded samples.
    """

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
        """Start time of the session.

        Returns:
            datetime: Local datetime the session started.
        """
        return datetime.fromtimestamp(self.started_at)


def _connect(path: Path) -> sqlite3.Connection:
    """Open a session database in WAL mode with row access by name.

    Args:
        path (Path): Path of the SQLite file.

    Returns:
        sqlite3.Connection: The open connection.
    """
    con = sqlite3.connect(path, isolation_level=None)  # autocommit; we wrap writes explicitly
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.row_factory = sqlite3.Row
    return con


class Recorder:
    """Records one sample per second to a session SQLite file.

    Attributes:
        path (Path): Path of the session SQLite file.
        con (sqlite3.Connection): Open database connection.
        distance_m (float): Accumulated distance in metres.
        lap (int): Current lap number.
    """

    def __init__(self, path: Path, con: sqlite3.Connection) -> None:
        """Initialize the recorder, restoring lap and distance from existing samples.

        Args:
            path (Path): Path of the session SQLite file.
            con (sqlite3.Connection): Open database connection.
        """
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
        """Create a new session file.

        Args:
            directory (Path): Directory where the session file is created.
            name (str): Session name.
            ftp (int): Rider's FTP in watts.
            workout (Workout | None): Workout being ridden, if any.
            now (float | None): Unix start time; defaults to ``time.time()``.

        Returns:
            Recorder: Recorder for the new session.
        """
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
        """Open an existing session file.

        Args:
            path (Path): Path of the session SQLite file.

        Returns:
            Recorder: Recorder for the session.
        """
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
        """Write one sample and update the session progress in a single transaction.

        Args:
            snap (Snapshot): Effective metrics for this second.
            elapsed (float): Active workout (or ride) seconds.
            target (float | None): Target power in watts, if any.
            paused (bool): Whether the ride is paused.
            mode (str): Current trainer control mode.
            now (float | None): Unix time of the sample; defaults to ``time.time()``.
        """
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
        """Start a new lap."""
        self.lap += 1

    def set_status(self, status: str, **fields) -> None:
        """Update the session status and optional extra columns.

        Args:
            status (str): New status (``"active"``, ``"finished"`` or ``"uploaded"``).
            **fields (Any): Additional session columns to set (e.g. ``upload_id``).
        """
        sets = ", ".join(["status=?"] + [f"{k}=?" for k in fields])
        self.con.execute(f"UPDATE session SET {sets} WHERE id=1", (status, *fields.values()))

    def close(self) -> None:
        """Close the database connection."""
        self.con.close()

    def info(self) -> SessionInfo:
        """Read the session metadata.

        Returns:
            SessionInfo: Metadata of this session.
        """
        return read_info(self.path, self.con)

    def samples(self) -> list[sqlite3.Row]:
        """Read all recorded samples.

        Returns:
            list[sqlite3.Row]: Samples ordered by time.
        """
        return self.con.execute("SELECT * FROM samples ORDER BY t").fetchall()


def read_info(path: Path, con: sqlite3.Connection | None = None) -> SessionInfo:
    """Read the metadata of a session file.

    Args:
        path (Path): Path of the session SQLite file.
        con (sqlite3.Connection | None): Existing connection to reuse; a temporary
            one is opened (and closed) if omitted.

    Returns:
        SessionInfo: Metadata of the session.
    """
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
    """List all readable sessions in a directory.

    Args:
        directory (Path): Directory containing session files.

    Returns:
        list[SessionInfo]: Sessions, most recent first.
    """
    out = []
    for p in sorted(directory.glob("session-*.sqlite"), reverse=True):
        try:
            out.append(read_info(p))
        except Exception:
            continue
    return out


def find_unfinished(directory: Path) -> list[SessionInfo]:
    """Sessions left ``active`` (app crashed or was killed while riding).

    Args:
        directory (Path): Directory containing session files.

    Returns:
        list[SessionInfo]: Unfinished sessions, most recent first.
    """
    return [s for s in list_sessions(directory) if s.status == "active"]
