"""Talks to the training platform through a :class:`SyncProvider`, whichever it is."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from smart_trainer.config import Settings
from smart_trainer.sync.base import SyncProvider

log = logging.getLogger(__name__)

ProviderFactory = Callable[[], SyncProvider | None]
"""Creates a provider, or returns ``None`` when no account is configured."""


NO_ACCOUNT = "No intervals.icu API key"


class SyncUnavailable(RuntimeError):
    """Raised when no provider can be used (demo mode or no account configured)."""


class SyncService:
    """Uploads activities and syncs the rider profile.

    The concrete platform is hidden behind the injected provider factory, so adding
    another platform means writing a new :class:`SyncProvider`, not changing this class.
    """

    def __init__(
        self,
        settings: Settings,
        messages: deque[str],
        provider_factory: ProviderFactory,
        ride_active: Callable[[], bool],
    ) -> None:
        """Initialize the service.

        Args:
            settings (Settings): User settings, updated by the profile sync.
            messages (deque[str]): User-facing status messages.
            provider_factory (ProviderFactory): Creates the sync provider.
            ride_active (Callable[[], bool]): Whether a ride is in progress.
        """
        self._settings = settings
        self._messages = messages
        self._factory = provider_factory
        self._ride_active = ride_active

    @asynccontextmanager
    async def provider(self) -> AsyncIterator[SyncProvider | None]:
        """Open a provider for the duration of a ``with`` block, closing it afterwards.

        Yields:
            SyncProvider | None: The provider, or ``None`` if no account is configured.
        """
        provider = self._factory()
        if provider is None:
            yield None
            return
        try:
            yield provider
        finally:
            await provider.aclose()

    async def upload(self, fit: Path, name: str, paired_event_id: str | None = None) -> str:
        """Upload a FIT activity.

        Args:
            fit (Path): Path of the FIT file.
            name (str): Activity name.
            paired_event_id (str | None): Planned event to pair the activity with.

        Returns:
            str: Id of the created activity.

        Raises:
            SyncUnavailable: If no provider can be used; a message explains why.
        """
        if self._settings._readonly:
            self._messages.append("Demo mode: uploads are disabled")
            raise SyncUnavailable("demo mode")
        async with self.provider() as p:
            if p is None:
                self._messages.append(f"{NO_ACCOUNT}: saved locally only")
                raise SyncUnavailable("no sync account")
            aid = await p.upload_activity(fit, name, paired_event_id=paired_event_id)
        self._messages.append(f"Uploaded to {p.name} ({aid})")
        return aid

    async def sync_profile(self) -> list[str] | None:
        """Update FTP, weight and zones from the provider and save the settings.

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
        async with self.provider() as p:
            if p is None:
                self._messages.append(f"{NO_ACCOUNT}: profile not synced")
                return None
            log.info("syncing profile from %s", p.name)
            try:
                profile = await p.profile()
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
            "profile synced: FTP %s W, weight %s kg, power zones %s, "
            "HR zones %s, LTHR %s, max HR %s (changed: %s)",
            st.ftp,
            st.weight_kg,
            st.power_zones,
            st.hr_zones,
            st.lthr,
            st.max_hr,
            ", ".join(changed) or "nothing",
        )
        return changed
