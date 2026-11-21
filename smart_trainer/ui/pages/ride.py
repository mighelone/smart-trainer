"""Ride page: live metrics, workout chart, ride and manual controls, crash recovery."""

from __future__ import annotations

from collections import deque

from nicegui import ui

from smart_trainer.config import Settings
from smart_trainer.core.control import Mode
from smart_trainer.core.ride import RideState
from smart_trainer.core.workout import WorkoutRunner
from smart_trainer.ui.context import AppContext, LiveRide
from smart_trainer.ui.pages.frame import frame
from smart_trainer.ui.pages.live import MainTiles, ride_status, start_ride
from smart_trainer.ui.pages.recovery import RecoveryDialog
from smart_trainer.ui.widgets.charts import LiveChart, workout_chart
from smart_trainer.ui.widgets.cues import CuePlayer
from smart_trainer.ui.widgets.text import fmt, fmt_time, next_step_text, step_title
from smart_trainer.ui.widgets.tiles import Tile

MANUAL_UNIT = {"resistance": "%", "slope": "% grade", "erg": " W"}
# Slider range per manual mode: resistance in %, slope in % grade, ERG in watts. Numbers, not
# a props string: `props("min=50")` sends "50" and Quasar's drag maths (`min + ratio * span`)
# then concatenates strings and throws, leaving the slider stuck mid-drag.
MANUAL_SLIDER = {
    Mode.RESISTANCE: {"min": 0, "max": 100, "step": 1},
    Mode.SLOPE: {"min": -10, "max": 20, "step": 0.5},
    Mode.ERG: {"min": 50, "max": 600, "step": 5},
}


class RidePage:
    """Ride page widgets: live metrics, workout chart and ride controls.

    Attributes:
        live_ride (LiveRide): The ride state shown and controlled.
        ride (Ride): The ride.
        controller (TrainerController): Trainer control (manual mode).
        devices (DeviceHub): Devices and their roles (power source).
        messages (deque[str]): Status messages from the core, shown after finishing.
        settings (Settings): User settings (FTP, zones, cues).
        ftp (int): FTP used to draw the workout chart.
    """

    def __init__(self, live: LiveRide, messages: deque[str], settings: Settings) -> None:
        """Build the page.

        Args:
            live (LiveRide): The ride state shown and controlled.
            messages (deque[str]): Status messages from the core.
            settings (Settings): User settings.
        """
        self.live_ride = live
        self.ride, self.controller, self.devices = live.ride, live.controller, live.devices
        self.messages = messages
        self.settings = settings
        self.ftp = settings.ftp
        self.chart = None
        self.live: LiveChart | None = None
        self.chart_workout: object = object()
        self.ticks = 0
        with ui.column().classes("page"):
            self.warn = ui.label("").classes("status-bar")
            self._build_tiles()
            self._build_step_card()
            self.holder = ui.card().classes("w-full p-2")
            self._build_controls()
            self._build_manual()
        self.cues = CuePlayer(self.ride, lambda: settings.interval_cues)

    # ------------------------------------------------------------- build
    def _build_tiles(self) -> None:
        """Build the two rows of metric tiles."""
        with ui.element("div").classes("metric-grid"):
            self.main_tiles = MainTiles(self.live_ride, self.settings)
        with ui.element("div").classes("metric-grid"):
            self.t_time = Tile("Elapsed", icon="schedule")
            self.t_step = Tile("Interval left", icon="timer")
            self.t_speed = Tile("Speed", "km/h", icon="speed")
            self.t_bal = Tile("Balance", "L/R", icon="balance")

    def _build_step_card(self) -> None:
        """Build the current/next interval card."""
        with ui.card().classes("w-full panel gap-2") as self.step_card:
            with ui.row().classes("w-full items-baseline justify-between no-wrap gap-4"):
                self.step_label = ui.label("").classes("step-title")
                self.next_label = ui.label("").classes("step-next text-right")
            self.step_bar = (
                ui.linear_progress(value=0, show_value=False)
                .props("rounded size=6px color=primary instant-feedback")
                .classes("step-progress")
            )

    def _build_controls(self) -> None:
        """Build the ride controls: start/pause/skip/lap, intensity, ERG and finish."""
        with ui.card().classes("w-full panel"), ui.row().classes("items-center gap-3 w-full"):
            self.btn_start = ui.button(
                "Start", icon="play_arrow", on_click=lambda: start_ride(self.ride)
            ).props("color=positive size=lg padding='8px 28px'")
            self.btn_pause = ui.button(
                "Pause", icon="pause", on_click=lambda: self.ride.toggle_pause()
            ).props("color=primary size=lg padding='8px 24px'")
            self.btn_skip = ui.button(
                "Skip", icon="skip_next", on_click=lambda: self.ride.skip_step()
            ).props("color=secondary")
            self.btn_lap = ui.button("Lap", icon="flag", on_click=lambda: self.ride.lap()).props(
                "color=secondary"
            )
            with ui.row().classes("items-center gap-1 ml-2") as self.intensity_box:
                ui.label("Intensity").classes("field-label mr-1")
                ui.button(icon="remove", on_click=lambda: self.ride.change_intensity(-0.05)).props(
                    "color=secondary round dense"
                ).tooltip("Intensity -5%")
                self.intensity = ui.label("100%").classes("control-value")
                ui.button(icon="add", on_click=lambda: self.ride.change_intensity(0.05)).props(
                    "color=secondary round dense"
                ).tooltip("Intensity +5%")
            self.erg = ui.switch(
                "ERG", value=self.ride.erg_enabled, on_change=lambda ev: self.ride.set_erg(ev.value)
            )
            ui.space()
            self.btn_finish = ui.button("Finish", icon="stop", on_click=self.confirm_finish).props(
                "color=negative outline size=lg padding='8px 24px'"
            )

    def _build_manual(self) -> None:
        """Build the manual control (mode and slider) and the power source toggle."""
        with ui.card().classes("w-full panel"), ui.row().classes("items-center gap-6 w-full"):
            with ui.row().classes("items-center gap-4 grow no-wrap") as self.manual_box:
                ui.label("Manual").classes("field-label")
                mode = self.controller.mode if self.ride.runner is None else Mode.RESISTANCE
                self.mode = (
                    ui.toggle(
                        {"resistance": "Resistance", "slope": "Slope", "erg": "Fixed ERG"},
                        value=mode.value,
                        on_change=lambda ev: self.set_manual_mode(ev.value),
                    )
                    .classes("shrink-0")
                    .mark("manual-mode")
                )
                self.slider = (
                    ui.slider(
                        **MANUAL_SLIDER[mode],
                        value=self.controller.target_for(mode),
                        on_change=lambda ev: self.set_manual_value(ev.value),
                    )
                    .classes("grow min-w-[120px]")
                    .mark("manual-slider")
                )
                self.slider_label = ui.label("").classes("control-value w-24")
            with ui.row().classes("items-center gap-3 shrink-0"):
                ui.label("Power source").classes("field-label")
                ui.toggle(
                    {"pedals": "Pedals", "trainer": "Trainer"},
                    value=self.settings.power_source,
                    on_change=lambda ev: self.devices.set_power_source(ev.value),
                )

    # ---------------------------------------------------------- handlers
    def set_manual_mode(self, value: str) -> None:
        """Switch the manual control mode and adapt the slider range.

        Args:
            value (str): Mode value (``"resistance"``, ``"slope"`` or ``"erg"``).
        """
        controller, mode = self.controller, Mode(value)
        controller.set_target(mode, controller.target_for(mode))
        self.slider.props.update(MANUAL_SLIDER[mode])
        self.slider.value = controller.target_for(mode)

    def set_manual_value(self, value: float) -> None:
        """Apply the slider value to the current manual mode.

        Args:
            value (float): Slider value (percent, grade or watts depending on mode).
        """
        controller = self.controller
        controller.set_target(controller.mode, value)

    async def do_finish(self, dialog: ui.dialog, upload: bool) -> None:
        """Finish the ride and notify the result.

        Args:
            dialog (ui.dialog): Confirmation dialog to close.
            upload (bool): Whether to upload the ride to the connected services.
        """
        dialog.close()
        notification = ui.notification("Saving session…", spinner=True, timeout=None)
        seen = len(self.messages)
        fit = await self.ride.finish(upload=upload)
        notification.dismiss()
        ui.notify(f"Saved {fit.name}" if fit else "Nothing recorded", type="positive")
        for message in list(self.messages)[seen:]:
            ui.notify(message)

    def confirm_finish(self) -> None:
        """Ask the rider how to finish the session."""
        with ui.dialog() as dialog, ui.card():
            ui.label("Finish this session?").classes("text-lg")
            with ui.row():
                ui.button(
                    "Save & upload",
                    icon="cloud_upload",
                    on_click=lambda: self.do_finish(dialog, True),
                ).props("color=positive")
                ui.button("Save only", icon="save", on_click=lambda: self.do_finish(dialog, False))
                ui.button("Keep riding", on_click=dialog.close).props("flat")
        dialog.open()

    # ----------------------------------------------------------- refresh
    def ensure_chart(self) -> None:
        """Recreate the chart when the displayed workout changes."""
        wo = (
            self.ride.runner.workout
            if self.ride.runner
            else (self.ride.next_workout if self.ride.state == RideState.IDLE else None)
        )
        if self.chart_workout is wo:
            return
        self.holder.clear()
        with self.holder:
            self.chart = workout_chart(wo, self.ftp)
        self.live = LiveChart(self.chart, self.ride.history, free_ride=wo is None)
        self.chart_workout = wo

    def refresh(self) -> None:
        """Update tiles, labels, buttons and controls from the engine state."""
        self.ensure_chart()
        snap = self.live_ride.snapshot()
        riding = self.ride.state == RideState.RIDING
        self.main_tiles.update()
        self.t_speed.set(fmt(snap.speed_kmh, 1))
        self.t_bal.set(
            f"{snap.balance_left:.0f}/{100 - snap.balance_left:.0f}" if snap.balance_left else "--"
        )
        runner = self.ride.runner
        if runner:
            self._show_workout_step(runner)
        else:
            self._show_no_workout(riding)
        status = ride_status(self.live_ride)
        self.warn.set_text(" · ".join(status))
        self.warn.set_visibility(bool(status))
        self._refresh_controls(riding, runner is not None)
        self._refresh_manual(manual=not (runner and self.ride.erg_enabled))
        self.cues.check()

    def _show_workout_step(self, runner: WorkoutRunner) -> None:
        """Show the elapsed time and the current/next interval of a running workout.

        Args:
            runner (WorkoutRunner): The workout runner.
        """
        index, st, into, left = runner.current()
        self.t_time.set(fmt_time(runner.elapsed), f"of {fmt_time(runner.workout.duration_s)}")
        self.t_step.set(fmt_time(left), f"step {index + 1} of {len(runner.workout.steps)}")
        self.step_label.set_text(step_title(index, st))
        nxt = runner.next_step()
        if nxt:
            self.next_label.set_text(f"Next · {next_step_text(nxt, runner.ftp, runner.intensity)}")
        else:
            self.next_label.set_text("Last interval")
        self.step_bar.set_value(min(1.0, into / st.duration_s) if st.duration_s else 0)
        self.intensity.set_text(f"{runner.intensity * 100:.0f}%")
        self.step_card.set_visibility(True)

    def _show_no_workout(self, riding: bool) -> None:
        """Show a free ride in progress, or the workout selected for the next ride.

        Args:
            riding (bool): Whether a (free) ride is in progress.
        """
        self.t_time.set(fmt_time(self.ride.seconds if riding else 0), "free ride" if riding else "")
        self.t_step.set("--")
        sel = None if riding else self.ride.next_workout
        self.step_label.set_text(sel.name if sel else "")
        self.next_label.set_text(
            f"{fmt_time(sel.duration_s)} · {len(sel.steps)} steps" if sel else ""
        )
        self.step_bar.set_value(0)
        self.step_card.set_visibility(sel is not None)

    def _refresh_controls(self, riding: bool, workout: bool) -> None:
        """Show the ride buttons that apply to the current state.

        Args:
            riding (bool): Whether a ride is in progress.
            workout (bool): Whether a workout is running.
        """
        self.btn_start.set_visibility(not riding)
        for button in (self.btn_pause, self.btn_finish, self.btn_lap):
            button.set_visibility(riding)
        self.btn_skip.set_visibility(riding and workout)
        self.btn_pause.set_text("Resume" if self.ride.manual_pause else "Pause")
        self.erg.set_visibility(workout)
        self.intensity_box.set_visibility(workout)

    def _refresh_manual(self, manual: bool) -> None:
        """Update the manual control, shown unless a workout drives the trainer in ERG.

        Args:
            manual (bool): Whether the manual control is in use.
        """
        self.manual_box.set_visibility(manual)
        controller = self.controller
        self.slider_label.set_text(
            f"{controller.target_for(controller.mode):g}{MANUAL_UNIT[controller.mode.value]}"
        )
        if manual and self.mode.value != controller.mode.value:
            self.mode.value = controller.mode.value

    def refresh_chart(self) -> None:
        """Send the new live power and heart-rate points to the chart."""
        if self.live is not None:
            self.live.sync()

    def loop(self) -> None:
        """Refresh the page, and the chart every other call."""
        self.refresh()
        self.ticks += 1
        if self.ticks % 2 == 0:
            self.refresh_chart()


def build_ride_page(ctx: AppContext) -> None:
    """Ride page: live metrics, workout chart and ride controls.

    Args:
        ctx (AppContext): The app's objects.
    """
    engine = ctx.engine
    if ctx.window.take_start_request():
        if not engine.ride.recoverable_sessions():
            ui.navigate.to("/mini")
            return
        ctx.window.restore_full()  # an unfinished session needs the full page's dialog
    frame(ctx, "Ride")
    page = RidePage(LiveRide.of(engine), engine.messages, ctx.settings)
    RecoveryDialog.offer(engine.ride, engine.store)
    ui.timer(0.5, page.loop)
