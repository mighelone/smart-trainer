"""Crash-safe session recording: one SQLite file per session, committed every second."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from smart_trainer.core.state import Snapshot
from smart_trainer.core.workout import Workout

log = logging.getLogger(__name__)

UPLOADS_SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    connection TEXT PRIMARY KEY,       -- connection id (intervals, strava, ...)
    remote_id TEXT,                    -- activity id once uploaded
    error TEXT,                        -- last error, if the upload failed
    updated_at REAL NOT NULL
);
"""
SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS session (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL,              -- active | finished (legacy: uploaded)
    started_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    name TEXT NOT NULL,
    ftp INTEGER NOT NULL,
    workout_json TEXT,
    elapsed_s REAL NOT NULL DEFAULT 0, -- active workout seconds (for resume)
    mode TEXT,
    upload_id TEXT,                    -- legacy (intervals.icu only), see uploads
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
    + UPLOADS_SCHEMA
)


@dataclass
class UploadState:
    """Outcome of uploading a session to one connection.

    Attributes:
        remote_id (str | None): Activity id on the platform once uploaded.
        error (str | None): Last error message, if the upload failed.
    """

    remote_id: str | None = None
    error: str | None = None

    @property
    def done(self) -> bool:
        """Whether the upload succeeded.

        Returns:
            bool: ``True`` if uploaded.
        """
        return bool(self.remote_id)


@dataclass
class SessionInfo:
    """Metadata of a recorded session.

    Attributes:
        path (Path): Path of the session SQLite file.
        status (str): ``"active"`` or ``"finished"``.
        name (str): Session name.
        started_at (float): Unix time the session started.
        updated_at (float): Unix time of the last recorded sample.
        ftp (int): FTP used for the session, in watts.
        elapsed_s (float): Active workout seconds (for resume).
        workout (Workout | None): Workout being ridden, if any.
        n_samples (int): Number of recorded samples.
        uploads (dict[str, UploadState]): Upload outcome by connection id.
    """

    path: Path
    status: str
    name: str
    started_at: float
    updated_at: float
    ftp: int
    elapsed_s: float
    workout: Workout | None
    n_samples: int
    uploads: dict[str, UploadState] = field(default_factory=dict)

    def uploaded(self, conn_id: str) -> bool:
        """Whether the session was uploaded to a connection.

        Args:
            conn_id (str): Connection id.

        Returns:
            bool: ``True`` if uploaded.
        """
        state = self.uploads.get(conn_id)
        return bool(state and state.done)

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
    try:
        con.execute("PRAGMA journal_mode=WAL")  # fails first on a file that isn't a database
        con.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        con.close()
        raise
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
                    snap.power_mean,  # the mean over the second, not the last reading
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
            status (str): New status (``"active"`` or ``"finished"``).
            **fields (Any): Additional session columns to set.
        """
        sets = ", ".join(["status=?"] + [f"{k}=?" for k in fields])
        self.con.execute(f"UPDATE session SET {sets} WHERE id=1", (status, *fields.values()))

    def set_upload(
        self,
        conn_id: str,
        remote_id: str | None = None,
        error: str | None = None,
        now: float | None = None,
    ) -> None:
        """Record the outcome of an upload to a connection.

        Args:
            conn_id (str): Connection id.
            remote_id (str | None): Activity id if the upload succeeded.
            error (str | None): Error message if it failed.
            now (float | None): Unix time of the outcome; defaults to ``time.time()``.
        """
        self.con.executescript(UPLOADS_SCHEMA)  # sessions recorded before uploads existed
        self.con.execute(
            "INSERT OR REPLACE INTO uploads (connection, remote_id, error, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (conn_id, remote_id, error, now or time.time()),
        )

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
        row = con.execute("SELECT * FROM session WHERE id=1").fetchone()
        n_samples = con.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        wo = Workout.from_dict(json.loads(row["workout_json"])) if row["workout_json"] else None
        return SessionInfo(
            path,
            "finished" if row["status"] == "uploaded" else row["status"],
            row["name"],
            row["started_at"],
            row["updated_at"],
            row["ftp"],
            row["elapsed_s"],
            wo,
            n_samples,
            _read_uploads(con, row),
        )
    finally:
        if own:
            con.close()


def _read_uploads(con: sqlite3.Connection, session: sqlite3.Row) -> dict[str, UploadState]:
    """Read the upload outcomes, including the legacy intervals.icu-only columns.

    Args:
        con (sqlite3.Connection): Open session database.
        session (sqlite3.Row): The session row.

    Returns:
        dict[str, UploadState]: Upload outcome by connection id.
    """
    out: dict[str, UploadState] = {}
    try:
        for upload in con.execute("SELECT connection, remote_id, error FROM uploads"):
            out[upload["connection"]] = UploadState(upload["remote_id"], upload["error"])
    except sqlite3.OperationalError:  # older session file without the uploads table
        pass
    if "intervals" not in out and (session["upload_id"] or session["upload_error"]):
        out["intervals"] = UploadState(session["upload_id"], session["upload_error"])
    return out


def list_sessions(directory: Path) -> list[SessionInfo]:
    """List all readable sessions in a directory.

    Args:
        directory (Path): Directory containing session files.

    Returns:
        list[SessionInfo]: Sessions, most recent first.
    """
    out = []
    for path in sorted(directory.glob("session-*.sqlite"), reverse=True):
        try:
            out.append(read_info(path))
        except Exception as exc:
            log.warning("skipping unreadable session %s: %s", path.name, exc)
    return out


def find_unfinished(directory: Path) -> list[SessionInfo]:
    """Sessions left ``active`` (app crashed or was killed while riding).

    Args:
        directory (Path): Directory containing session files.

    Returns:
        list[SessionInfo]: Unfinished sessions, most recent first.
    """
    return [s for s in list_sessions(directory) if s.status == "active"]
