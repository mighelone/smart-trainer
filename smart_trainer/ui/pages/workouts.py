"""Workouts page: planned workouts from the connections, built-ins and .zwo import."""

from __future__ import annotations

from datetime import date, timedelta

from nicegui import ui

from smart_trainer.config import Settings
from smart_trainer.core.library import builtin_workouts
from smart_trainer.core.ride import Ride
from smart_trainer.core.workout import Workout, parse_zwo
from smart_trainer.sync.base import Capability
from smart_trainer.sync.service import SyncService
from smart_trainer.ui.widgets import fmt_time, workout_chart


class WorkoutsPage:
    """Workouts page widgets: planned workouts, built-ins and .zwo import.

    Attributes:
        ride (Ride): The ride (the workout is selected for it).
        sync (SyncService): Connections that provide planned workouts.
        settings (Settings): User settings (FTP and zones for the workout targets).
    """

    def __init__(self, ride: Ride, sync: SyncService, settings: Settings) -> None:
        """Build the page and schedule loading the planned workouts.

        Args:
            ride (Ride): The ride.
            sync (SyncService): Connections that provide planned workouts.
            settings (Settings): User settings.
        """
        self.ride = ride
        self.sync = sync
        self.settings = settings
        with ui.column().classes("page"):
            if selected := ride.next_workout:
                with ui.row().classes("items-center gap-2"):
                    ui.icon("check_circle", color="positive")
                    ui.label(f"Selected: {selected.name}").classes("list-title")
            self._build_toolbar()
            self.status = ui.label("").classes("hint")
            self.listing = ui.column().classes("w-full gap-3")
            ui.label("Built-in workouts").classes("section-title mt-4")
            with ui.column().classes("w-full gap-3"):
                for wo in builtin_workouts():
                    self.workout_card(wo, wo.description)
        ui.timer(0.1, self.load, once=True)

    def _build_toolbar(self) -> None:
        """Build the title row with the refresh, free ride and import buttons."""
        with ui.row().classes("items-center gap-2 w-full"):
            sources = self.sync.with_capability(Capability.WORKOUTS)
            ui.label(
                "Planned on " + " & ".join(c.name for c in sources) if sources else "Planned"
            ).classes("section-title")
            ui.space()
            ui.button("Refresh", icon="refresh", on_click=self.load).props("color=secondary")
            ui.button("Free ride", icon="all_inclusive", on_click=lambda: self.choose(None)).props(
                "color=secondary"
            )
            uploader = (
                ui.upload(auto_upload=True, on_upload=self.import_zwo)
                .props("accept=.zwo")
                .classes("hidden")
            )
            ui.button(
                "Import .zwo", icon="upload_file", on_click=lambda: uploader.run_method("pickFiles")
            ).props("color=secondary")

    def workout_card(self, wo: Workout, subtitle: str) -> None:
        """Render a workout card with its profile and a "Ride this" button.

        Args:
            wo (Workout): The workout to show.
            subtitle (str): Text shown under the workout name.
        """
        with ui.card().classes("w-full panel gap-2"):
            with ui.row().classes("items-center w-full"):
                with ui.column().classes("gap-0"):
                    ui.label(wo.name).classes("list-title")
                    ui.label(
                        f"{subtitle} · {fmt_time(wo.duration_s)} · {len(wo.steps)} steps"
                    ).classes("list-sub")
                ui.space()
                ui.button(
                    "Ride this", icon="play_arrow", on_click=lambda w=wo: self.choose(w)
                ).props("color=positive")
            workout_chart(wo, self.settings.ftp, height="h-28", live=False)

    def choose(self, wo: Workout | None) -> None:
        """Select a workout for the next ride and go to the ride page.

        Args:
            wo (Workout | None): The workout, or ``None`` for a free ride.
        """
        self.ride.select(wo)
        ui.notify(f"Selected {wo.name}" if wo else "Free ride selected")
        ui.navigate.to("/")

    async def import_zwo(self, ev) -> None:
        """Parse an uploaded ``.zwo`` file and select it.

        Args:
            ev (UploadEventArguments): The upload event.
        """
        try:
            wo = parse_zwo(await ev.file.text())
        except Exception as exc:
            ui.notify(f"Invalid .zwo file: {exc}", type="negative")
            return
        self.choose(wo)

    async def load(self) -> None:
        """Load planned workouts from the connected services into the listing."""
        self.listing.clear()
        sources = self.sync.connected(Capability.WORKOUTS)
        if not sources:
            names = " or ".join(c.name for c in self.sync.with_capability(Capability.WORKOUTS))
            self.status.set_text(
                f"Connect {names or 'a service'} in Settings to load planned workouts."
            )
            return
        self.status.set_text("Loading…")
        today = date.today()
        planned, errors = [], []
        for conn in sources:
            try:
                planned += await conn.list_workouts(
                    today - timedelta(days=2),
                    today + timedelta(days=7),
                    self.settings.ftp,
                    self.settings.power_zones,
                )
            except Exception as exc:
                errors.append(f"Could not load workouts from {conn.name}: {exc}")
        planned.sort(key=lambda p: p.day)
        text = (
            f"{len(planned)} cycling workouts planned"
            if planned
            else "No cycling workouts planned in the next 7 days."
        )
        self.status.set_text(" · ".join(errors) if errors and not planned else text)
        with self.listing:
            for p in planned:
                label = "Today" if p.day == date.today() else p.day.strftime("%a %d %b")
                self.workout_card(p.workout, label)
