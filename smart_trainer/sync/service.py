"""Talks to the training platforms through their :class:`Connection`, whichever they are."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from smart_trainer.config import Settings
from smart_trainer.sync.base import Capability, Connection, RideUpload

log = logging.getLogger(__name__)

ConnectionsFactory = Callable[[], list[Connection]]
"""Returns the available connections (configured or not)."""

# The ride keeps the FTP it started with, so the profile is never synced during one
RIDE_ACTIVE_SKIP = "Profile sync skipped during a ride"


@dataclass(frozen=True)
class UploadResult:
    """Outcome of uploading a ride to one connection.

    Attributes:
        connection_id (str): Id of the connection.
        connection_name (str): Its display name.
        activity_id (str | None): Id of the created activity, on success.
        error (str | None): Why the upload failed, or ``None`` on success.
    """

    connection_id: str
    connection_name: str
    activity_id: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the upload succeeded.

        Returns:
            bool: ``True`` without an error.
        """
        return self.error is None

    @property
    def message(self) -> str:
        """User-facing summary of the outcome.

        Returns:
            str: e.g. ``"Uploaded to Strava (123)"`` or ``"Strava upload failed: …"``.
        """
        if self.ok:
            return f"Uploaded to {self.connection_name} ({self.activity_id})"
        return f"{self.connection_name} upload failed: {self.error}"


@dataclass(frozen=True)
class UploadReport:
    """What happened when a ride was uploaded.

    Attributes:
        results (tuple[UploadResult, ...]): One result per connection tried.
        skipped (str | None): Why nothing was tried (demo mode, no connection), or ``None``.
    """

    results: tuple[UploadResult, ...] = ()
    skipped: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the upload was attempted and every connection succeeded.

        Returns:
            bool: ``False`` if skipped or if any connection failed.
        """
        return self.skipped is None and all(result.ok for result in self.results)

    @property
    def messages(self) -> list[str]:
        """User-facing lines describing the report.

        Returns:
            list[str]: The skip reason, or one line per connection.
        """
        if self.skipped:
            return [self.skipped]
        return [result.message for result in self.results]


@dataclass(frozen=True)
class ProfileSyncResult:
    """What happened when the rider profile was synced.

    Attributes:
        changed (list[str]): Labels of the fields that changed (empty if none did).
        skipped (str | None): Why the profile wasn't synced, or ``None`` if it was.
    """

    changed: list[str] = field(default_factory=list)
    skipped: str | None = None


class SyncService:
    """Uploads activities to the connections and syncs the rider profile.

    The concrete platforms are hidden behind the injected connections factory, so adding
    another platform means writing a new :class:`Connection`, not changing this class.
    """

    def __init__(
        self,
        settings: Settings,
        connections: ConnectionsFactory,
        ride_active: Callable[[], bool],
    ) -> None:
        """Initialize the service.

        Args:
            settings (Settings): User settings, updated by the profile sync.
            connections (ConnectionsFactory): Returns the available connections.
            ride_active (Callable[[], bool]): Whether a ride is in progress.
        """
        self._settings = settings
        self._factory = connections
        self._ride_active = ride_active

    def connections(self) -> list[Connection]:
        """All available connections, configured or not.

        Returns:
            list[Connection]: The connections.
        """
        return self._factory()

    def with_capability(self, capability: Capability) -> list[Connection]:
        """Connections that support a capability, configured or not.

        Args:
            capability (Capability): The capability.

        Returns:
            list[Connection]: Matching connections.
        """
        return [c for c in self.connections() if c.has(capability)]

    def connected(self, capability: Capability) -> list[Connection]:
        """Enabled, configured connections that support a capability.

        Args:
            capability (Capability): The capability.

        Returns:
            list[Connection]: Usable connections.
        """
        return [c for c in self.with_capability(capability) if c.usable]

    def auto_upload_targets(self) -> list[str]:
        """Connections a finished ride is uploaded to automatically.

        Returns:
            list[str]: Connection ids (empty if automatic upload is off).
        """
        if not self._settings.auto_upload:
            return []
        return [c.id for c in self.connected(Capability.UPLOAD) if c.conf.auto_upload]

    async def upload(
        self,
        ride: RideUpload,
        targets: list[str] | None = None,
        skip: set[str] | frozenset[str] = frozenset(),
    ) -> UploadReport:
        """Upload a ride to the connections, concurrently; one failing doesn't stop the others.

        Args:
            ride (RideUpload): The ride to upload.
            targets (list[str] | None): Connection ids to upload to; defaults to every
                enabled, configured upload connection.
            skip (set[str] | frozenset[str]): Connection ids to leave out (already uploaded).

        Returns:
            UploadReport: One result per connection tried, or why nothing was tried.
        """
        if self._settings.readonly:
            return UploadReport(skipped="Demo mode: uploads are disabled")
        conns = [
            conn
            for conn in self.connected(Capability.UPLOAD)
            if (targets is None or conn.id in targets) and conn.id not in skip
        ]
        if not conns:
            return UploadReport(skipped="No connection to upload to: saved locally only")
        results = await asyncio.gather(*(self._upload_one(conn, ride) for conn in conns))
        return UploadReport(results=tuple(results))

    @staticmethod
    async def _upload_one(conn: Connection, ride: RideUpload) -> UploadResult:
        """Upload a ride to one connection, capturing the error.

        Args:
            conn (Connection): The connection.
            ride (RideUpload): The ride.

        Returns:
            UploadResult: The activity id, or the error message.
        """
        try:
            activity_id = await conn.upload_activity(ride)
        except Exception as exc:
            log.warning("upload of %s to %s failed: %s", ride.fit_path.name, conn.name, exc)
            return UploadResult(conn.id, conn.name, error=str(exc) or type(exc).__name__)
        return UploadResult(conn.id, conn.name, activity_id=activity_id)

    async def sync_profile(self) -> ProfileSyncResult:
        """Update FTP, weight and zones from the profile connection and save the settings.

        Skipped during a ride, so the ride keeps the FTP it started with.

        Returns:
            ProfileSyncResult: The labels of the changed fields, or why the sync didn't
            happen.
        """
        if self._settings.readonly:
            return ProfileSyncResult(skipped="Demo mode: profile sync is disabled")
        if self._ride_active():
            return ProfileSyncResult(skipped=RIDE_ACTIVE_SKIP)
        conns = self.connected(Capability.PROFILE)
        if not conns:
            return ProfileSyncResult(skipped="No profile connection configured: profile not synced")
        conn = conns[0]
        log.info("syncing profile from %s", conn.name)
        try:
            profile = await conn.profile()
        except Exception as exc:
            log.warning("profile sync failed: %s", exc)
            return ProfileSyncResult(skipped=f"Profile sync failed: {exc}")
        if self._ride_active():  # a ride started while we were waiting
            return ProfileSyncResult(skipped=RIDE_ACTIVE_SKIP)
        st = self._settings
        changed = profile.apply(st)
        st.profile_synced_at = time.time()
        st.save()
        log.info(
            "profile synced from %s: FTP %s W, weight %s kg, power zones %s, "
            "HR zones %s, LTHR %s, max HR %s (changed: %s)",
            conn.name,
            st.ftp,
            st.weight_kg,
            st.power_zones,
            st.hr_zones,
            st.lthr,
            st.max_hr,
            ", ".join(changed) or "nothing",
        )
        return ProfileSyncResult(changed=changed)
