"""Mini overlay: a compact always-on-top window with key metrics and basic controls."""

from __future__ import annotations

from nicegui import ui

from smart_trainer.config import Settings
from smart_trainer.core.ride import RideState
from smart_trainer.core.workout import WorkoutRunner
from smart_trainer.ui.context import LiveRide
from smart_trainer.ui.pages.live import MainTiles, ride_status, start_ride
from smart_trainer.ui.theme import apply_theme
from smart_trainer.ui.widgets.cues import CuePlayer
from smart_trainer.ui.widgets.text import fmt_time, step_title
from smart_trainer.ui.widgets.tiles import Tile
from smart_trainer.ui.window import MiniWindow


class MiniPage:
    """Compact always-on-top overlay: key metrics and basic ride controls."""

    def __init__(self, live: LiveRide, settings: Settings, window: MiniWindow) -> None:
        """Build the overlay.

        Args:
            live (LiveRide): The ride state shown and controlled.
            settings (Settings): User settings (FTP, zones, cues).
            window (MiniWindow): Full app / mini overlay switcher.
        """
        self.live = live
        self.ride = live.ride
        self.window = window
        apply_theme()
        with ui.column().classes("mini-page"):
            with ui.row().classes("w-full no-wrap items-stretch gap-2"):
                with ui.element("div").classes("metric-grid mini-grid"):
                    self.main_tiles = MainTiles(live, settings, compact=True)
                    self.t_step = Tile("Interval left", icon="timer", size="sm")
                self._build_controls()
            self._build_step_line()
        self.cues = CuePlayer(self.ride, lambda: settings.interval_cues)

    def _build_controls(self) -> None:
        """Build the start/pause, intensity and back-to-full-app buttons."""
        ride = self.ride
        with ui.column().classes("gap-1 justify-center shrink-0"):
            self.btn_start = (
                ui.button(icon="play_arrow", on_click=lambda: start_ride(ride))
                .props("color=positive dense")
                .mark("mini-start")
                .tooltip("Start")
            )
            self.btn_pause = (
                ui.button(icon="pause", on_click=lambda: ride.toggle_pause())
                .props("color=primary dense")
                .mark("mini-pause")
            )
            with ui.row().classes("gap-1 no-wrap") as self.intensity_box:
                ui.button(icon="remove", on_click=lambda: ride.change_intensity(-0.05)).props(
                    "color=secondary dense"
                ).tooltip("Intensity -5%")
                ui.button(icon="add", on_click=lambda: ride.change_intensity(0.05)).props(
                    "color=secondary dense"
                ).mark("mini-plus").tooltip("Intensity +5%")
            ui.button(icon="open_in_full", on_click=self.window.exit).props(
                "color=secondary dense"
            ).mark("mini-expand").tooltip("Back to the full app")

    def _build_step_line(self) -> None:
        """Build the step line and its progress bar."""
        with ui.row().classes("w-full items-center no-wrap gap-3"):
            self.step_label = ui.label("").classes("mini-step")
            self.step_bar = (
                ui.linear_progress(value=0, show_value=False)
                .props("rounded size=5px color=primary instant-feedback")
                .classes("step-progress grow")
            )

    def refresh(self) -> None:
        """Update tiles, step line and buttons from the engine state."""
        self.cues.check()
        riding = self.ride.state == RideState.RIDING
        self.main_tiles.update()
        runner = self.ride.runner
        text = self._show_workout_step(runner) if runner else self._show_no_workout(riding)
        status = ride_status(self.live)
        self.step_label.set_text(" · ".join(status) if status else text)
        self.step_label.classes(
            add="mini-warn" if status else "", remove="" if status else "mini-warn"
        )
        self._show_buttons(riding, has_workout=runner is not None)

    def _show_workout_step(self, runner: WorkoutRunner) -> str:
        """Show the current step in the interval tile and progress bar.

        Args:
            runner (WorkoutRunner): The workout being ridden.

        Returns:
            str: The step line: step title and progress through the workout.
        """
        index, step, into, left = runner.current()
        self.t_step.set(
            fmt_time(left), f"{index + 1}/{len(runner.workout.steps)} · {runner.intensity:.0%}"
        )
        self.step_bar.set_value(min(1.0, into / step.duration_s) if step.duration_s else 0)
        return (
            f"{step_title(index, step)} · {fmt_time(runner.elapsed)}"
            f" of {fmt_time(runner.workout.duration_s)}"
        )

    def _show_no_workout(self, riding: bool) -> str:
        """Show a free ride, or the workout selected for the next ride.

        Args:
            riding (bool): Whether a ride is in progress.

        Returns:
            str: The step line.
        """
        self.t_step.set("--", "free ride" if riding else "")
        self.step_bar.set_value(0)
        if riding:
            return f"Free ride · {fmt_time(self.ride.seconds)}"
        selected = self.ride.next_workout
        return selected.name if selected else "Free ride: press start"

    def _show_buttons(self, riding: bool, has_workout: bool) -> None:
        """Show start or pause, and the intensity buttons while riding a workout.

        Args:
            riding (bool): Whether a ride is in progress.
            has_workout (bool): Whether the ride follows a workout.
        """
        self.btn_start.set_visibility(not riding)
        self.btn_pause.set_visibility(riding)
        self.btn_pause.props(f"icon={'play_arrow' if self.ride.manual_pause else 'pause'}")
        self.intensity_box.set_visibility(riding and has_workout)
