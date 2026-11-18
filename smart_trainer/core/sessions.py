"""Session files on disk: creation, finalization (FIT export, upload), recovery."""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

from smart_trainer.config import Settings
from smart_trainer.core.recorder import Recorder, SessionInfo, find_unfinished
from smart_trainer.core.workout import Workout
from smart_trainer.export.fit import export_fit
from smart_trainer.sync.base import RideUpload
from smart_trainer.sync.service import SyncService, SyncUnavailable

log = logging.getLogger(__name__)


class SessionStore:
    """Manages the recorded sessions in one directory.

    Attributes:
        directory (Path): Directory where sessions are recorded.
    """

    def __init__(
        self, directory: Path, settings: Settings, sync: SyncService, messages: deque[str]
    ) -> None:
        """Initialize the store.

        Args:
            directory (Path): Directory where sessions are recorded.
            settings (Settings): User settings (FTP, ``auto_upload``).
            sync (SyncService): Uploads finished sessions.
            messages (deque[str]): User-facing status messages.
        """
        self.directory = directory
        self._settings = settings
        self._sync = sync
        self._messages = messages

    def create(self, name: str, workout: Workout | None) -> Recorder:
        """Create a new session file.

        Args:
            name (str): Session name.
            workout (Workout | None): Workout being ridden, if any.

        Returns:
            Recorder: Recorder for the new session.
        """
        return Recorder.create(self.directory, name, self._settings.ftp, workout)

    def open(self, info: SessionInfo) -> Recorder:
        """Open an existing session file.

        Args:
            info (SessionInfo): The session to open.

        Returns:
            Recorder: Recorder for the session.
        """
        return Recorder.open(info.path)

    async def finalize(self, rec: Recorder, upload: bool | None = None) -> Path | None:
        """Export a session to FIT, mark it finished and optionally upload it.

        Args:
            rec (Recorder): The session recorder.
            upload (bool | None): ``True`` uploads to every connected upload target,
                ``False`` never uploads, ``None`` follows the automatic upload settings.

        Returns:
            Path | None: The FIT file path, or ``None`` if the session has no samples.
        """
        info = rec.info()
        fit = None
        if info.n_samples:
            fit = export_fit(rec, rec.path.with_suffix(".fit"))
        rec.set_status("finished")
        if fit and upload is None:
            if targets := self._sync.auto_upload_targets():
                await self.upload(rec, fit, info, targets)
        elif fit and upload:
            await self.upload(rec, fit, info)
        return fit

    async def upload(
        self,
        rec: Recorder,
        fit: Path,
        info: SessionInfo | None = None,
        targets: list[str] | None = None,
    ) -> dict[str, bool]:
        """Upload a FIT file to the connections and record each outcome in the session.

        Connections the session was already uploaded to are skipped.

        Args:
            rec (Recorder): The session recorder.
            fit (Path): Path of the FIT file to upload.
            info (SessionInfo | None): Session info; read from ``rec`` if omitted.
            targets (list[str] | None): Connection ids to upload to; defaults to every
                enabled, configured upload connection.

        Returns:
            dict[str, bool]: Whether the upload succeeded, by connection id (empty if
            nothing was attempted; a message explains why).
        """
        info = info or rec.info()
        ride = RideUpload(
            fit,
            info.name,
            started_at=info.started_at,
            workout_source_id=info.workout.source_id if info.workout else None,
        )
        done = {cid for cid, state in info.uploads.items() if state.done}
        try:
            outcomes = await self._sync.upload(ride, targets, skip=done)
        except SyncUnavailable:
            return {}  # nothing was attempted: the session stays as it is
        for cid, (aid, err) in outcomes.items():
            rec.set_upload(cid, aid, err)
        return {cid: err is None for cid, (_, err) in outcomes.items()}

    async def upload_session(self, info: SessionInfo) -> dict[str, bool]:
        """Upload an already finished session where it's still missing.

        Args:
            info (SessionInfo): The session; its FIT file must exist.

        Returns:
            dict[str, bool]: Whether the upload succeeded, by connection id.
        """
        rec = self.open(info)
        try:
            return await self.upload(rec, info.path.with_suffix(".fit"), info)
        finally:
            rec.close()

    async def finish_crashed(self, info: SessionInfo, upload: bool | None = None) -> Path | None:
        """Finalize a crashed session without resuming it.

        Args:
            info (SessionInfo): The unfinished session.
            upload (bool | None): ``True`` uploads to every connected upload target,
                ``False`` never uploads, ``None`` follows the automatic upload settings.

        Returns:
            Path | None: The FIT file path, or ``None`` if the session has no samples.
        """
        rec = self.open(info)
        try:
            return await self.finalize(rec, upload)
        finally:
            rec.close()

    def unfinished(self, exclude: Path | None = None) -> list[SessionInfo]:
        """List sessions still marked ``active`` (i.e. the app crashed during them).

        Args:
            exclude (Path | None): A session to leave out (the one recording now).

        Returns:
            list[SessionInfo]: The unfinished sessions.
        """
        return [s for s in find_unfinished(self.directory) if s.path != exclude]

    def discard(self, info: SessionInfo) -> None:
        """Move a session out of the way (kept in sessions/discarded, never deleted).

        Args:
            info (SessionInfo): The session to discard.
        """
        dest = self.directory / "discarded"
        dest.mkdir(exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(info.path) + suffix)
            if p.exists():
                p.replace(dest / p.name)
