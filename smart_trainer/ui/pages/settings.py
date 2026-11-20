"""Settings page: rider profile, power matching, ride options and connections."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any

from nicegui import ui

from smart_trainer.config import Settings
from smart_trainer.core.control import SPIRAL_RELEASE_RPM, SPIRAL_RESUME_RPM
from smart_trainer.sync.base import Capability
from smart_trainer.sync.service import SyncService
from smart_trainer.ui.connections import connection_card
from smart_trainer.ui.widgets import CuePlayer

log = logging.getLogger(__name__)


def profile_summary(st: Settings) -> str:
    """Describe the synced zones and the last sync time.

    Args:
        st (Settings): The settings to describe.

    Returns:
        str: Multi-line summary.
    """
    lines = []
    if st.power_zones:
        parts = []
        for i, pct in enumerate(st.power_zones):
            name = f"Z{i + 1}"
            if i == len(st.power_zones) - 1:
                lower = st.power_zones[i - 1] if i else 0
                parts.append(f"{name} ≥{round(lower * st.ftp / 100)} W")
            else:
                parts.append(f"{name} <{round(pct * st.ftp / 100)} W")
        lines.append("Power zones: " + " · ".join(parts))
    if st.hr_zones:
        lines.append("HR zones: " + " · ".join(f"Z{i + 1} ≤{b}" for i, b in enumerate(st.hr_zones)))
    hr = [f"LTHR {st.lthr} bpm" if st.lthr else "", f"max HR {st.max_hr} bpm" if st.max_hr else ""]
    if any(hr):
        lines.append(" · ".join(x for x in hr if x))
    when = (
        datetime.fromtimestamp(st.profile_synced_at).strftime("%Y-%m-%d %H:%M")
        if st.profile_synced_at
        else "never"
    )
    lines.append(f"Last synced: {when}")
    return "\n".join(lines)


class SettingsPage:
    """Settings page widgets: rider profile, power matching and connections.

    Attributes:
        sync_service (SyncService): Connections (profile sync, connection cards).
        messages (deque[str]): Status messages from the core, shown when a sync fails.
        st (Settings): User settings edited by the page.
    """

    def __init__(
        self,
        sync: SyncService,
        messages: deque[str],
        settings: Settings,
        set_spiral_guard: Callable[[bool], None],
    ) -> None:
        """Build the page.

        Args:
            sync (SyncService): Connections (profile sync, connection cards).
            messages (deque[str]): Status messages from the core.
            settings (Settings): User settings.
            set_spiral_guard (Callable[[bool], None]): Turns the ERG spiral guard on or off
                (saves the setting and applies it to the trainer control).
        """
        self.sync_service = sync
        self.messages = messages
        self._set_spiral_guard = set_spiral_guard
        self.st = settings
        with (
            ui.column().classes("page page-narrow [&_.q-field]:w-full"),
            ui.card().classes("w-full panel gap-3"),
        ):
            self._build_profile()
            ui.separator()
            self._build_power_match()
            ui.separator()
            self._build_ride()
            ui.separator()
            self._build_connections()

    # ------------------------------------------------------------- build
    def _build_profile(self) -> None:
        """Build the rider profile section (FTP, weight and profile sync)."""
        st = self.st
        profile_sources = self.sync_service.with_capability(Capability.PROFILE)
        source = " or ".join(c.name for c in profile_sources) or "your training platform"
        ui.label("Rider profile").classes("section-title")
        self.sync = ui.switch(f"Sync FTP, zones and weight from {source}", value=st.sync_profile)
        self.ftp = ui.number("FTP (W)", value=st.ftp, min=50, max=600, step=1).mark("ftp")
        self.weight = ui.number("Weight (kg)", value=st.weight_kg, min=30, max=200, step=0.5)
        self.summary = ui.label("").classes("hint whitespace-pre-line")
        self.sync_btn = ui.button("Sync now", icon="sync", on_click=self.sync_now).props(
            "color=secondary"
        )
        self.sync.on_value_change(self.toggle_sync)
        self.refresh_profile()

    def _build_power_match(self) -> None:
        """Build the power matching section and the Save button."""
        pm = self.st.power_match
        ui.label("Power matching (pedals as power source)").classes("section-title mt-2")
        self.max_off = ui.number("Max correction (W)", value=pm.max_offset_w, min=0, max=150).mark(
            "max-offset"
        )
        self.settle = ui.number(
            "Settle time after target change (s)", value=pm.settle_s, min=0, max=30
        )
        ui.button("Save", icon="save", on_click=self.save).props("color=positive").classes(
            "mt-2"
        ).mark("save-settings")

    def _build_ride(self) -> None:
        """Build the ride section: interval cues and the ERG spiral guard."""
        ui.label("Ride").classes("section-title mt-2")
        with ui.row().classes("items-center gap-2"):
            self.cues = ui.switch(
                "Beep and show the next interval 5 s before it starts",
                value=self.st.interval_cues,
                on_change=self.toggle_cues,
            ).mark("interval-cues")
            ui.button(
                "Test sound", icon="volume_up", on_click=lambda: CuePlayer.beep(ui.context.client)
            ).props("flat color=secondary")
        CuePlayer.install()
        ui.switch(
            f"Release ERG when cadence drops below {SPIRAL_RELEASE_RPM} rpm",
            value=self.st.erg_spiral_guard,
            on_change=lambda ev: self.toggle_spiral_guard(bool(ev.value)),
        ).mark("erg-spiral-guard").tooltip(
            "Avoids grinding to a halt: the trainer goes to a flat road until you spin "
            f"back up to {SPIRAL_RESUME_RPM} rpm, then the target comes back"
        )

    def _build_connections(self) -> None:
        """Build the connections section: automatic upload and one card per connection."""
        ui.label("Connections").classes("section-title mt-2")
        self.auto = ui.switch(
            "Upload rides automatically when finished",
            value=self.st.auto_upload,
            on_change=self.toggle_auto,
        ).tooltip("Each connection below can opt out of automatic uploads")
        for conn in self.sync_service.connections():
            ui.separator()
            connection_card(conn, self.credentials_changed)

    # ---------------------------------------------------------- profile
    def refresh_profile(self) -> None:
        """Show the current profile and lock the fields while syncing is on."""
        st = self.st
        self.ftp.value, self.weight.value = st.ftp, st.weight_kg
        on = bool(self.sync.value)
        for number in (self.ftp, self.weight):
            if on:
                number.props("readonly")
            else:
                number.props(remove="readonly")
        self.sync_btn.set_visibility(on)
        self.summary.set_visibility(on)
        self.summary.set_text(profile_summary(st))

    async def sync_now(self) -> None:
        """Fetch the profile and show the result."""
        self.sync_btn.props("loading")
        try:
            changed = await self.sync_service.sync_profile()
        finally:
            self.sync_btn.props(remove="loading")
        if changed is None:
            ui.notify(self.messages[-1] if self.messages else "Sync failed", type="negative")
        else:
            text = f"Updated {', '.join(changed)}" if changed else "Already up to date"
            ui.notify(text, type="positive")
        self.refresh_profile()

    def can_sync(self) -> bool:
        """Whether a profile connection is usable.

        Returns:
            bool: ``True`` if the profile can be synced.
        """
        return bool(self.sync_service.connected(Capability.PROFILE))

    async def toggle_sync(self) -> None:
        """Persist the sync switch; sync right away when it is turned on."""
        st = self.st
        st.update(sync_profile=bool(self.sync.value))
        self.refresh_profile()
        if st.sync_profile and self.can_sync():
            await self.sync_now()

    async def credentials_changed(self) -> None:
        """Sync the profile once a connection gets its credentials."""
        if self.st.sync_profile and self.can_sync():
            await self.sync_now()

    # ------------------------------------------------------------- save
    def save(self) -> None:
        """Validate the form values and persist the settings."""
        # ui.number yields None when a field is cleared: keep the current value then.
        changes: dict[str, Any] = {}
        if not self.st.sync_profile:  # synced values come from the profile connection only
            if self.ftp.value is not None:
                changes["ftp"] = int(self.ftp.value)
            if self.weight.value is not None:
                changes["weight_kg"] = float(self.weight.value)
        power_match = {}
        if self.max_off.value is not None:
            power_match["max_offset_w"] = float(self.max_off.value)
        if self.settle.value is not None:
            power_match["settle_s"] = float(self.settle.value)
        try:
            self.st.update(**changes, power_match=power_match)
        except ValueError as exc:
            log.warning("invalid settings: %s", exc)
            ui.notify("Invalid settings: not saved", type="negative")
            return
        ui.notify("Settings saved", type="positive")

    def toggle_cues(self) -> None:
        """Persist the interval cues switch."""
        self.st.update(interval_cues=bool(self.cues.value))

    def toggle_spiral_guard(self, on: bool) -> None:
        """Persist the ERG spiral guard switch and apply it to the ride in progress.

        Args:
            on (bool): Whether the guard is on.
        """
        self._set_spiral_guard(on)

    def toggle_auto(self) -> None:
        """Persist the global automatic upload switch."""
        self.st.update(auto_upload=bool(self.auto.value))
