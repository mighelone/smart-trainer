"""Talks to the training platforms through their :class:`Connection`, whichever they are."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable

from smart_trainer.config import Settings
from smart_trainer.sync.base import Capability, Connection, RideUpload

log = logging.getLogger(__name__)

ConnectionsFactory = Callable[[], list[Connection]]
"""Returns the available connections (configured or not)."""

UploadOutcome = tuple[str | None, str | None]
"""``(activity id, None)`` on success, ``(None, error message)`` on failure."""


class SyncUnavailable(RuntimeError):
    """Raised when nothing can be synced (demo mode or no connection configured)."""


class SyncService:
    """Uploads activities to the connections and syncs the rider profile.

    The concrete platforms are hidden behind the injected connections factory, so adding
    another platform means writing a new :class:`Connection`, not changing this class.
    """

    def __init__(
        self,
        settings: Settings,
        messages: deque[str],
        connections: ConnectionsFactory,
        ride_active: Callable[[], bool],
    ) -> None:
        """Initialize the service.

        Args:
            settings (Settings): User settings, updated by the profile sync.
            messages (deque[str]): User-facing status messages.
            connections (ConnectionsFactory): Returns the available connections.
            ride_active (Callable[[], bool]): Whether a ride is in progress.
        """
        self._settings = settings
        self._messages = messages
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
    ) -> dict[str, UploadOutcome]:
        """Upload a ride to the connections, concurrently; one failing doesn't stop the others.

        Args:
            ride (RideUpload): The ride to upload.
            targets (list[str] | None): Connection ids to upload to; defaults to every
                enabled, configured upload connection.
            skip (set[str] | frozenset[str]): Connection ids to leave out (already uploaded).

        Returns:
            dict[str, UploadOutcome]: Outcome by connection id.

        Raises:
            SyncUnavailable: If nothing can be uploaded; a message explains why.
        """
        if self._settings._readonly:
            self._messages.append("Demo mode: uploads are disabled")
            raise SyncUnavailable("demo mode")
        conns = [
            c
            for c in self.connected(Capability.UPLOAD)
            if (targets is None or c.id in targets) and c.id not in skip
        ]
        if not conns:
            self._messages.append("No connection to upload to: saved locally only")
            raise SyncUnavailable("no connection")
        outcomes = await asyncio.gather(*(self._upload_one(c, ride) for c in conns))
        result: dict[str, UploadOutcome] = {}
        for c, (aid, err) in zip(conns, outcomes, strict=True):
            result[c.id] = (aid, err)
            self._messages.append(
                f"Uploaded to {c.name} ({aid})" if err is None else f"{c.name} upload failed: {err}"
            )
        return result

    @staticmethod
    async def _upload_one(conn: Connection, ride: RideUpload) -> UploadOutcome:
        """Upload a ride to one connection, capturing the error.

        Args:
            conn (Connection): The connection.
            ride (RideUpload): The ride.

        Returns:
            UploadOutcome: The activity id, or the error message.
        """
        try:
            return await conn.upload_activity(ride), None
        except Exception as exc:
            log.warning("upload of %s to %s failed: %s", ride.fit_path.name, conn.name, exc)
            return None, str(exc) or type(exc).__name__

    async def sync_profile(self) -> list[str] | None:
        """Update FTP, weight and zones from the profile connection and save the settings.

        Skipped during a ride, so the ride keeps the FTP it started with.

        Returns:
            list[str] | None: Labels of the changed fields, or ``None`` if the sync
            didn't happen (a message explains why).
        """
        if self._settings._readonly:
            self._messages.append("Demo mode: profile sync is disabled")
            return None
        if self._ride_active():
            self._messages.append("Profile sync skipped during a ride")
            return None
        conns = self.connected(Capability.PROFILE)
        if not conns:
            self._messages.append("No profile connection configured: profile not synced")
            return None
        conn = conns[0]
        log.info("syncing profile from %s", conn.name)
        try:
            profile = await conn.profile()
        except Exception as exc:
            log.warning("profile sync failed: %s", exc)
            self._messages.append(f"Profile sync failed: {exc}")
            return None
        if self._ride_active():  # a ride started while we were waiting
            self._messages.append("Profile sync skipped during a ride")
            return None
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
        return changed
