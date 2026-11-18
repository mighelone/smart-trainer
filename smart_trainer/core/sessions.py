"""Session files on disk: creation, finalization (FIT export, upload), recovery."""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

from smart_trainer.config import Settings
from smart_trainer.core.recorder import Recorder, SessionInfo, find_unfinished
from smart_trainer.core.workout import Workout
from smart_trainer.export.fit import export_fit
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
            upload (bool | None): Whether to upload; defaults to the
                ``auto_upload`` setting.

        Returns:
            Path | None: The FIT file path, or ``None`` if the session has no samples.
        """
        info = rec.info()
        fit = None
        if info.n_samples:
            fit = export_fit(rec, rec.path.with_suffix(".fit"))
        rec.set_status("finished")
        if fit and (self._settings.auto_upload if upload is None else upload):
            await self.upload(rec, fit, info)
        return fit

    async def upload(self, rec: Recorder, fit: Path, info: SessionInfo | None = None) -> bool:
        """Upload a FIT file and record the outcome in the session.

        Args:
            rec (Recorder): The session recorder.
            fit (Path): Path of the FIT file to upload.
            info (SessionInfo | None): Session info; read from ``rec`` if omitted.

        Returns:
            bool: ``True`` if the upload succeeded.
        """
        info = info or rec.info()
        try:
            aid = await self._sync.upload(
                fit, info.name, paired_event_id=info.workout.source_id if info.workout else None
            )
        except SyncUnavailable:
            return False  # nothing was attempted: the session status stays as it is
        except Exception as exc:
            log.warning("upload of %s failed: %s", fit.name, exc)
            rec.set_status("finished", upload_error=str(exc))
            self._messages.append(f"Upload failed: {exc}")
            return False
        rec.set_status("uploaded", upload_id=aid, upload_error=None)
        return True

    async def upload_session(self, info: SessionInfo) -> bool:
        """Upload an already finished session.

        Args:
            info (SessionInfo): The session; its FIT file must exist.

        Returns:
            bool: ``True`` if the upload succeeded.
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
            upload (bool | None): Whether to upload; defaults to the
                ``auto_upload`` setting.

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
