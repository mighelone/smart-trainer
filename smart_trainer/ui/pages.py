"""NiceGUI pages. The UI only reads Engine state and calls Engine methods."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast

from nicegui import app, ui

from smart_trainer.ble.device import (
    DRIVERS,
    Calibratable,
    CalibrationKind,
    CalibrationPhase,
    CalibrationProgress,
    Device,
)
from smart_trainer.config import Settings
from smart_trainer.core.control import (
    SPIRAL_RELEASE_RPM,
    SPIRAL_RESUME_RPM,
    Mode,
    SpiralGuard,
)
from smart_trainer.core.engine import Engine
from smart_trainer.core.library import builtin_workouts
from smart_trainer.core.recorder import SessionInfo, UploadState
from smart_trainer.core.ride import RideState
from smart_trainer.core.state import ConnState, Role
from smart_trainer.core.workout import Workout, WorkoutRunner, parse_zwo
from smart_trainer.sync.base import Capability, Connection
from smart_trainer.ui.connections import connection_card
from smart_trainer.ui.theme import apply_theme
from smart_trainer.ui.widgets import (
    CuePlayer,
    LiveChart,
    Tile,
    cadence_display,
    fmt,
    fmt_time,
    hr_zone,
    next_step_text,
    power_zone,
    step_title,
    workout_chart,
)

if TYPE_CHECKING:
    from smart_trainer.ble.scanner import Discovered

log = logging.getLogger(__name__)


class AppState:
    """Global UI state shared by all pages.

    Attributes:
        engine (Engine): The application engine.
        settings (Settings): User settings (shared with the engine's components).
        selected (Workout | None): Workout selected for the next ride.
        fake (bool): Whether the app runs with simulated devices.
    """

    engine: Engine
    settings: Settings
    selected: Workout | None = None
    fake: bool = False
    start_mini: bool = False
    full_geometry: tuple[tuple[int, int], tuple[int, int]] | None = None


S = AppState()

MINI_SIZE = (1000, 210)  # native window size of the mini overlay (incl. title bar)
FULL_SIZE = (1280, 860)  # size restored when the app was started in mini mode

DOT_COLOR = {
    ConnState.CONNECTED: "#22c55e",
    ConnState.CONNECTING: "#f59e0b",
    ConnState.DISCONNECTED: "#ef4444",
    None: "#5b6774",
}
ROLE_ICON = {
    Role.TRAINER: "pedal_bike",
    Role.POWER: "bolt",
    Role.HR: "favorite",
    Role.CADENCE: "autorenew",
    Role.SPEED: "speed",
}


def frame(title: str) -> None:
    """Common header with navigation and device status chips.

    Args:
        title (str): Page title shown in the header.
    """
    apply_theme()
    with ui.header().classes("app-header items-center justify-between px-6 py-2"):
        with ui.row().classes("items-center gap-3"):
            ui.icon("directions_bike", size="sm").classes("text-primary")
            ui.label("Smart Trainer").classes("brand")
            if S.fake:
                ui.badge("Demo", color="warning").props("outline").tooltip("Simulated devices")
        with ui.row().classes("gap-1"):
            for label, path in (
                ("Ride", "/"),
                ("Workouts", "/workouts"),
                ("Devices", "/devices"),
                ("History", "/history"),
                ("Settings", "/settings"),
            ):
                active = label == title
                ui.button(label, on_click=lambda p=path: ui.navigate.to(p)).props(
                    f"flat dense padding='6px 14px' color={'white' if active else 'grey-5'}"
                ).classes("nav-btn" + (" active" if active else ""))
            ui.button(icon="picture_in_picture_alt", on_click=enter_mini).props(
                "flat dense padding='6px 10px' color=grey-5"
            ).classes("nav-btn").mark("mini").tooltip("Mini overlay: stays on top of other windows")
        chips = DeviceChips(S.engine)
    ui.timer(2.0, chips.refresh)


class DeviceChips:
    """Header chips with the state of the trainer, power meter and HR strap.

    The chips are built once; :meth:`refresh` only changes the dot colour and the
    tooltip of a chip when they change (NiceGUI then sends just that).

    Attributes:
        ROLES (tuple[Role, ...]): Roles shown, in order.
        e (Engine): The application engine.
    """

    ROLES = (Role.TRAINER, Role.POWER, Role.HR)

    def __init__(self, e: Engine) -> None:
        """Build the chips and show the current state.

        Args:
            e (Engine): The application engine.
        """
        self.e = e
        self._dots: dict[Role, ui.element] = {}
        self._tips: dict[Role, ui.tooltip] = {}
        with ui.row().classes("gap-2"):
            for role in self.ROLES:
                with ui.element("div").classes("dev-chip").mark(f"chip-{role.value}"):
                    ui.icon(ROLE_ICON[role], size="16px")
                    self._dots[role] = ui.element("span").classes("dev-dot")
                    self._tips[role] = ui.tooltip("")
        self.refresh()

    @staticmethod
    def describe(role: Role, dev: Device | None) -> tuple[str, str]:
        """Colour and tooltip of a chip.

        Args:
            role (Role): The role shown.
            dev (Device | None): The device in that role, if one is paired.

        Returns:
            tuple[str, str]: The dot colour and the tooltip text.
        """
        if dev is None:
            return DOT_COLOR[None], f"{role.value}: not paired"
        return DOT_COLOR[dev.state], f"{role.value}: {dev.name} ({dev.state})"

    def refresh(self) -> None:
        """Update the chips from the devices (unchanged chips send nothing)."""
        for role in self.ROLES:
            color, tip = self.describe(role, self.e.devices.device_for(role))
            self._dots[role].style(replace=f"background: {color}")
            self._tips[role].set_text(tip)


# ============================================================ WINDOW MODES
class NativeWindow(Protocol):
    """The part of NiceGUI's native ``WindowProxy`` used here.

    NiceGUI falls back to an empty ``WindowProxy`` class when pywebview is missing, which hides
    these methods from type checkers.
    """

    async def get_size(self) -> tuple[int, int]: ...
    async def get_position(self) -> tuple[int, int]: ...
    def set_always_on_top(self, on_top: bool) -> None: ...
    def resize(self, width: int, height: int) -> None: ...
    def move(self, x: int, y: int) -> None: ...


def native_window() -> NativeWindow | None:
    """The native desktop window, or ``None`` when running in a browser.

    Returns:
        NativeWindow | None: The window proxy.
    """
    return cast("NativeWindow | None", app.native.main_window)


async def enter_mini() -> None:
    """Switch to the mini overlay: small, always-on-top window (native) and the /mini page."""
    w = native_window()
    if w is not None:
        S.full_geometry = (await w.get_size(), await w.get_position())
        w.set_always_on_top(True)
        w.resize(*MINI_SIZE)
    ui.navigate.to("/mini")


def restore_full_window() -> None:
    """Restore the normal window: not on top, previous size and position (native only)."""
    w = native_window()
    if w is None:
        return
    w.set_always_on_top(False)
    if S.full_geometry:
        (width, height), (x, y) = S.full_geometry
        w.resize(width, height)
        w.move(x, y)
    else:
        w.resize(*FULL_SIZE)


def exit_mini() -> None:
    """Leave the mini overlay and go back to the full ride page."""
    restore_full_window()
    ui.navigate.to("/")


def start_ride() -> None:
    """Start a ride with the selected workout (or a free ride)."""
    try:
        S.engine.ride.start(S.selected)
    except RuntimeError as exc:
        ui.notify(str(exc), type="warning")
        return
    ui.notify(f"Started: {S.selected.name if S.selected else 'free ride'}")


def ride_status(e: Engine) -> list[str]:
    """Status messages for the ride (pause, auto-pause, ERG release, device warnings).

    Args:
        e (Engine): The application engine.

    Returns:
        list[str]: Messages to show, empty when all is well.
    """
    status = []
    if e.ride.manual_pause:
        status.append("PAUSED")
    elif e.ride.state == RideState.RIDING and e.ride.auto_paused:
        status.append("Auto-paused: start pedalling")
    elif e.ride.state == RideState.RIDING and e.controller.erg_released:
        status.append(f"ERG released: spin up to {SPIRAL_RESUME_RPM} rpm")
    elif e.ride.cadence_alert and e.ride.cadence_target is not None:
        cad = fmt(e.snapshot.cadence)
        status.append(f"Cadence {e.ride.cadence_alert}: {cad} / {e.ride.cadence_target} rpm")
    status += e.snapshot.warnings
    if e.devices.device_for(Role.TRAINER) and not e.devices.trainer_connected():
        status.append("Trainer disconnected: reconnecting…")
    return status


def update_main_tiles(e: Engine, t_power: Tile, t_target: Tile, t_hr: Tile, t_cad: Tile) -> None:
    """Update the power, target, heart-rate and cadence tiles from the engine state.

    Args:
        e (Engine): The application engine.
        t_power (Tile): 3 s power tile.
        t_target (Tile): Target power tile.
        t_hr (Tile): Heart-rate tile.
        t_cad (Tile): Cadence tile.
    """
    snap = e.snapshot
    ftp = S.settings.ftp
    assignment = e.devices.assignment
    p3 = snap.power_avg
    src = (
        "pedals"
        if assignment.has_pedals and snap.power_source == assignment.power_id
        else "trainer"
    )
    sub = f"from {src}" + (
        f" · trainer {fmt(snap.trainer_power)} W"
        if snap.pedal_power is not None and snap.trainer_power is not None
        else ""
    )
    zones = S.settings.power_zones
    t_power.set(fmt(p3), sub)
    t_power.set_zone(*power_zone(p3, ftp, zones))
    target = e.ride.current_target()
    cmd = e.controller.last_command
    t_target.set(
        fmt(target) if target is not None else "--",
        f"trainer command {cmd[1]:.0f} W"
        if cmd and cmd[0] == Mode.ERG
        else f"{e.controller.mode.value} mode",
    )
    t_target.set_zone(*power_zone(target, ftp, zones))
    max_hr = S.settings.max_hr
    t_hr.set(fmt(snap.hr), f"{snap.hr / max_hr:.0%} of max" if snap.hr and max_hr else "")
    t_hr.set_zone(*hr_zone(snap.hr, S.settings.hr_zones))
    cad_sub, cad_accent, cad_badge = cadence_display(e.ride.cadence_target, e.ride.cadence_alert)
    t_cad.set(fmt(snap.cadence), cad_sub)
    t_cad.set_zone(cad_accent, cad_badge)


# ===================================================================== RIDE
MANUAL_UNIT = {"resistance": "%", "slope": "% grade", "erg": " W"}
# Slider range per manual mode: resistance in %, slope in % grade, ERG in watts
MANUAL_SLIDER = {
    Mode.RESISTANCE: "min=0 max=100 step=1",
    Mode.SLOPE: "min=-10 max=20 step=0.5",
    Mode.ERG: "min=50 max=600 step=5",
}


class RidePage:
    """Ride page widgets: live metrics, workout chart and ride controls.

    Attributes:
        e (Engine): The application engine.
        ftp (int): FTP used to draw the workout chart.
    """

    def __init__(self, e: Engine) -> None:
        """Build the page.

        Args:
            e (Engine): The application engine.
        """
        self.e = e
        self.ftp = S.settings.ftp
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
        self.cues = CuePlayer(e.ride, lambda: S.settings.interval_cues)

    # ------------------------------------------------------------- build
    def _build_tiles(self) -> None:
        """Build the two rows of metric tiles."""
        with ui.element("div").classes("metric-grid"):
            self.t_power = Tile("Power · 3s", "W", icon="bolt", size="lg", accent="#facc15")
            self.t_target = Tile("Target", "W", icon="adjust", size="lg", accent="#4f8cff")
            self.t_hr = Tile("Heart rate", "bpm", icon="favorite", size="lg", accent="#f43f5e")
            self.t_cad = Tile("Cadence", "rpm", icon="autorenew", size="lg", accent="#a78bfa")
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
        e = self.e
        with ui.card().classes("w-full panel"), ui.row().classes("items-center gap-3 w-full"):
            self.btn_start = ui.button("Start", icon="play_arrow", on_click=start_ride).props(
                "color=positive size=lg padding='8px 28px'"
            )
            self.btn_pause = ui.button(
                "Pause", icon="pause", on_click=lambda: e.ride.toggle_pause()
            ).props("color=primary size=lg padding='8px 24px'")
            self.btn_skip = ui.button(
                "Skip", icon="skip_next", on_click=lambda: e.ride.skip_step()
            ).props("color=secondary")
            self.btn_lap = ui.button("Lap", icon="flag", on_click=lambda: e.ride.lap()).props(
                "color=secondary"
            )
            with ui.row().classes("items-center gap-1 ml-2") as self.intensity_box:
                ui.label("Intensity").classes("field-label mr-1")
                ui.button(icon="remove", on_click=lambda: e.ride.change_intensity(-0.05)).props(
                    "color=secondary round dense"
                ).tooltip("Intensity -5%")
                self.intensity = ui.label("100%").classes("control-value")
                ui.button(icon="add", on_click=lambda: e.ride.change_intensity(0.05)).props(
                    "color=secondary round dense"
                ).tooltip("Intensity +5%")
            self.erg = ui.switch(
                "ERG", value=e.ride.erg_enabled, on_change=lambda ev: e.ride.set_erg(ev.value)
            )
            ui.space()
            self.btn_finish = ui.button("Finish", icon="stop", on_click=self.confirm_finish).props(
                "color=negative outline size=lg padding='8px 24px'"
            )

    def _build_manual(self) -> None:
        """Build the manual control (mode and slider) and the power source toggle."""
        e = self.e
        with ui.card().classes("w-full panel"), ui.row().classes("items-center gap-6 w-full"):
            with ui.row().classes("items-center gap-4 grow no-wrap") as self.manual_box:
                ui.label("Manual").classes("field-label")
                self.mode = (
                    ui.toggle(
                        {"resistance": "Resistance", "slope": "Slope", "erg": "Fixed ERG"},
                        value=e.controller.mode.value if e.ride.runner is None else "resistance",
                        on_change=lambda ev: self.set_manual_mode(ev.value),
                    )
                    .classes("shrink-0")
                    .mark("manual-mode")
                )
                self.slider = (
                    ui.slider(
                        min=0,
                        max=100,
                        step=1,
                        value=e.controller.resistance_pct,
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
                    value=S.settings.power_source,
                    on_change=lambda ev: e.devices.set_power_source(ev.value),
                )

    # ---------------------------------------------------------- handlers
    def set_manual_mode(self, m: str) -> None:
        """Switch the manual control mode and adapt the slider range.

        Args:
            m (str): Mode value (``"resistance"``, ``"slope"`` or ``"erg"``).
        """
        controller, mode = self.e.controller, Mode(m)
        controller.set_target(mode, controller.target_for(mode))
        self.slider.props(MANUAL_SLIDER[mode])
        self.slider.value = controller.target_for(mode)

    def set_manual_value(self, v: float) -> None:
        """Apply the slider value to the current manual mode.

        Args:
            v (float): Slider value (percent, grade or watts depending on mode).
        """
        controller = self.e.controller
        controller.set_target(controller.mode, v)

    async def do_finish(self, dialog: ui.dialog, upload: bool) -> None:
        """Finish the ride and notify the result.

        Args:
            dialog (ui.dialog): Confirmation dialog to close.
            upload (bool): Whether to upload the ride to the connected services.
        """
        e = self.e
        dialog.close()
        n = ui.notification("Saving session…", spinner=True, timeout=None)
        seen = len(e.messages)
        fit = await e.ride.finish(upload=upload)
        n.dismiss()
        S.selected = None
        ui.notify(f"Saved {fit.name}" if fit else "Nothing recorded", type="positive")
        for m in list(e.messages)[seen:]:
            ui.notify(m)

    def confirm_finish(self) -> None:
        """Ask the rider how to finish the session."""
        with ui.dialog() as d, ui.card():
            ui.label("Finish this session?").classes("text-lg")
            with ui.row():
                ui.button(
                    "Save & upload", icon="cloud_upload", on_click=lambda: self.do_finish(d, True)
                ).props("color=positive")
                ui.button("Save only", icon="save", on_click=lambda: self.do_finish(d, False))
                ui.button("Keep riding", on_click=d.close).props("flat")
        d.open()

    # ----------------------------------------------------------- refresh
    def ensure_chart(self) -> None:
        """Recreate the chart when the displayed workout changes."""
        e = self.e
        wo = (
            e.ride.runner.workout
            if e.ride.runner
            else (S.selected if e.ride.state == RideState.IDLE else None)
        )
        if self.chart_workout is wo:
            return
        self.holder.clear()
        with self.holder:
            self.chart = workout_chart(wo, self.ftp)
        self.live = LiveChart(self.chart, e.ride.history, free_ride=wo is None)
        self.chart_workout = wo

    def refresh(self) -> None:
        """Update tiles, labels, buttons and controls from the engine state."""
        e = self.e
        self.ensure_chart()
        snap = e.snapshot
        riding = e.ride.state == RideState.RIDING
        update_main_tiles(e, self.t_power, self.t_target, self.t_hr, self.t_cad)
        self.t_speed.set(fmt(snap.speed_kmh, 1))
        self.t_bal.set(
            f"{snap.balance_left:.0f}/{100 - snap.balance_left:.0f}" if snap.balance_left else "--"
        )
        r = e.ride.runner
        if r:
            self._show_workout_step(r)
        else:
            self._show_no_workout(riding)
        status = ride_status(e)
        self.warn.set_text(" · ".join(status))
        self.warn.set_visibility(bool(status))
        self._refresh_controls(riding, r is not None)
        self._refresh_manual(manual=not (r and e.ride.erg_enabled))
        self.cues.check()

    def _show_workout_step(self, r: WorkoutRunner) -> None:
        """Show the elapsed time and the current/next interval of a running workout.

        Args:
            r (WorkoutRunner): The workout runner.
        """
        i, st, into, left = r.current()
        self.t_time.set(fmt_time(r.elapsed), f"of {fmt_time(r.workout.duration_s)}")
        self.t_step.set(fmt_time(left), f"step {i + 1} of {len(r.workout.steps)}")
        self.step_label.set_text(step_title(i, st))
        nxt = r.next_step()
        if nxt:
            self.next_label.set_text(f"Next · {next_step_text(nxt, r.ftp, r.intensity)}")
        else:
            self.next_label.set_text("Last interval")
        self.step_bar.set_value(min(1.0, into / st.duration_s) if st.duration_s else 0)
        self.intensity.set_text(f"{r.intensity * 100:.0f}%")
        self.step_card.set_visibility(True)

    def _show_no_workout(self, riding: bool) -> None:
        """Show a free ride in progress, or the workout selected for the next ride.

        Args:
            riding (bool): Whether a (free) ride is in progress.
        """
        e = self.e
        self.t_time.set(fmt_time(e.ride.seconds if riding else 0), "free ride" if riding else "")
        self.t_step.set("--")
        sel = None if riding else S.selected
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
        for b in (self.btn_pause, self.btn_finish, self.btn_lap):
            b.set_visibility(riding)
        self.btn_skip.set_visibility(riding and workout)
        self.btn_pause.set_text("Resume" if self.e.ride.manual_pause else "Pause")
        self.erg.set_visibility(workout)
        self.intensity_box.set_visibility(workout)

    def _refresh_manual(self, manual: bool) -> None:
        """Update the manual control, shown unless a workout drives the trainer in ERG.

        Args:
            manual (bool): Whether the manual control is in use.
        """
        self.manual_box.set_visibility(manual)
        c = self.e.controller
        self.slider_label.set_text(f"{c.target_for(c.mode):g}{MANUAL_UNIT[c.mode.value]}")
        if manual and self.mode.value != c.mode.value:
            self.mode.value = c.mode.value

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


def recovery_dialog(e: Engine) -> None:
    """Offer to resume, finish or discard an unfinished session.

    Args:
        e (Engine): The application engine.
    """
    if e.ride.state != RideState.IDLE:
        return
    crashed = e.ride.recoverable_sessions()
    if not crashed:
        return
    info = crashed[0]
    with ui.dialog().props("persistent") as d, ui.card():
        ui.label("Unfinished session found").classes("text-xl font-bold")
        ui.label(
            f"{info.name}, started {info.started:%a %d %b %H:%M}, "
            f"{fmt_time(info.elapsed_s)} recorded ({info.n_samples} samples)"
        )
        ui.label("The app was closed or crashed during this ride.").classes("opacity-70")

        def resume():
            """Resume the unfinished session."""
            try:
                e.ride.resume(info)
            except RuntimeError as exc:
                ui.notify(str(exc), type="warning")
                return
            d.close()
            ui.notify("Session resumed: start pedalling")

        async def finish():
            """Finalize the unfinished session without resuming it."""
            d.close()
            fit = await e.store.finish_crashed(info)
            ui.notify(f"Saved {fit.name if fit else 'session'}")

        def discard():
            """Move the unfinished session to the discarded folder."""
            d.close()
            e.store.discard(info)
            ui.notify("Session moved to sessions/discarded")

        with ui.row():
            ui.button("Resume", icon="play_arrow", on_click=resume).props("color=positive")
            ui.button("Finish & save", icon="save", on_click=finish)
            ui.button("Discard", icon="delete", on_click=discard).props("flat color=negative")
    d.open()


@ui.page("/")
def ride_page() -> None:
    """Ride page: live metrics, workout chart and ride controls."""
    e = S.engine
    if S.start_mini:
        S.start_mini = False
        if not e.ride.recoverable_sessions():
            ui.navigate.to("/mini")
            return
        restore_full_window()  # an unfinished session needs the full page's dialog
    frame("Ride")
    page = RidePage(e)
    recovery_dialog(e)
    ui.timer(0.5, page.loop)


# ===================================================================== MINI
@ui.page("/mini")
def mini_page() -> None:
    """Compact always-on-top overlay: key metrics and basic ride controls."""
    e = S.engine
    apply_theme()
    with ui.column().classes("mini-page"):
        with ui.row().classes("w-full no-wrap items-stretch gap-2"):
            with ui.element("div").classes("metric-grid mini-grid"):
                t_power = Tile("Power", "W", icon="bolt", size="sm", accent="#facc15")
                t_target = Tile("Target", "W", icon="adjust", size="sm", accent="#4f8cff")
                t_hr = Tile("HR", "bpm", icon="favorite", size="sm", accent="#f43f5e")
                t_cad = Tile("Cadence", "rpm", icon="autorenew", size="sm", accent="#a78bfa")
                t_step = Tile("Interval left", icon="timer", size="sm")
            with ui.column().classes("gap-1 justify-center shrink-0"):
                btn_start = (
                    ui.button(icon="play_arrow", on_click=start_ride)
                    .props("color=positive dense")
                    .mark("mini-start")
                    .tooltip("Start")
                )
                btn_pause = (
                    ui.button(icon="pause", on_click=lambda: e.ride.toggle_pause())
                    .props("color=primary dense")
                    .mark("mini-pause")
                )
                with ui.row().classes("gap-1 no-wrap") as intensity_box:
                    ui.button(icon="remove", on_click=lambda: e.ride.change_intensity(-0.05)).props(
                        "color=secondary dense"
                    ).tooltip("Intensity -5%")
                    ui.button(icon="add", on_click=lambda: e.ride.change_intensity(0.05)).props(
                        "color=secondary dense"
                    ).mark("mini-plus").tooltip("Intensity +5%")
                ui.button(icon="open_in_full", on_click=exit_mini).props(
                    "color=secondary dense"
                ).mark("mini-expand").tooltip("Back to the full app")
        with ui.row().classes("w-full items-center no-wrap gap-3"):
            step_label = ui.label("").classes("mini-step")
            step_bar = (
                ui.linear_progress(value=0, show_value=False)
                .props("rounded size=5px color=primary instant-feedback")
                .classes("step-progress grow")
            )

    cues = CuePlayer(e.ride, lambda: S.settings.interval_cues)

    def refresh() -> None:
        """Update tiles, step line and buttons from the engine state."""
        cues.check()
        riding = e.ride.state == RideState.RIDING
        update_main_tiles(e, t_power, t_target, t_hr, t_cad)
        r = e.ride.runner
        if r:
            i, st, into, left = r.current()
            t_step.set(fmt_time(left), f"{i + 1}/{len(r.workout.steps)} · {r.intensity:.0%}")
            text = (
                f"{step_title(i, st)} · {fmt_time(r.elapsed)} of {fmt_time(r.workout.duration_s)}"
            )
            step_bar.set_value(min(1.0, into / st.duration_s) if st.duration_s else 0)
        else:
            t_step.set("--", "free ride" if riding else "")
            sel = None if riding else S.selected
            text = (
                f"Free ride · {fmt_time(e.ride.seconds)}"
                if riding
                else (sel.name if sel else "Free ride: press start")
            )
            step_bar.set_value(0)
        status = ride_status(e)
        step_label.set_text(" · ".join(status) if status else text)
        step_label.classes(add="mini-warn" if status else "", remove="" if status else "mini-warn")
        btn_start.set_visibility(not riding)
        btn_pause.set_visibility(riding)
        btn_pause.props(f"icon={'play_arrow' if e.ride.manual_pause else 'pause'}")
        intensity_box.set_visibility(riding and r is not None)

    refresh()
    ui.timer(0.5, refresh)


# ================================================================= WORKOUTS
def choose_workout(wo: Workout | None) -> None:
    """Select a workout for the next ride and go to the ride page.

    Args:
        wo (Workout | None): The workout, or ``None`` for a free ride.
    """
    S.selected = wo
    ui.notify(f"Selected {wo.name}" if wo else "Free ride selected")
    ui.navigate.to("/")


async def import_zwo(ev) -> None:
    """Parse an uploaded ``.zwo`` file and select it.

    Args:
        ev (UploadEventArguments): The upload event.
    """
    try:
        wo = parse_zwo(await ev.file.text())
    except Exception as exc:
        ui.notify(f"Invalid .zwo file: {exc}", type="negative")
        return
    choose_workout(wo)


def workout_card(wo: Workout, subtitle: str) -> None:
    """Render a workout card with its profile and a "Ride this" button.

    Args:
        wo (Workout): The workout to show.
        subtitle (str): Text shown under the workout name.
    """
    with ui.card().classes("w-full panel gap-2"):
        with ui.row().classes("items-center w-full"):
            with ui.column().classes("gap-0"):
                ui.label(wo.name).classes("list-title")
                ui.label(f"{subtitle} · {fmt_time(wo.duration_s)} · {len(wo.steps)} steps").classes(
                    "list-sub"
                )
            ui.space()
            ui.button(
                "Ride this", icon="play_arrow", on_click=lambda w=wo: choose_workout(w)
            ).props("color=positive")
        workout_chart(wo, S.settings.ftp, height="h-28", live=False)


class WorkoutsPage:
    """Workouts page widgets: planned workouts, built-ins and .zwo import.

    Attributes:
        e (Engine): The application engine.
    """

    def __init__(self, e: Engine) -> None:
        """Build the page and schedule loading the planned workouts.

        Args:
            e (Engine): The application engine.
        """
        self.e = e
        with ui.column().classes("page"):
            if S.selected:
                with ui.row().classes("items-center gap-2"):
                    ui.icon("check_circle", color="positive")
                    ui.label(f"Selected: {S.selected.name}").classes("list-title")
            self._build_toolbar()
            self.status = ui.label("").classes("hint")
            self.listing = ui.column().classes("w-full gap-3")
            ui.label("Built-in workouts").classes("section-title mt-4")
            with ui.column().classes("w-full gap-3"):
                for wo in builtin_workouts():
                    workout_card(wo, wo.description)
        ui.timer(0.1, self.load, once=True)

    def _build_toolbar(self) -> None:
        """Build the title row with the refresh, free ride and import buttons."""
        with ui.row().classes("items-center gap-2 w-full"):
            sources = self.e.sync.with_capability(Capability.WORKOUTS)
            ui.label(
                "Planned on " + " & ".join(c.name for c in sources) if sources else "Planned"
            ).classes("section-title")
            ui.space()
            ui.button("Refresh", icon="refresh", on_click=self.load).props("color=secondary")
            ui.button(
                "Free ride", icon="all_inclusive", on_click=lambda: choose_workout(None)
            ).props("color=secondary")
            uploader = (
                ui.upload(auto_upload=True, on_upload=import_zwo)
                .props("accept=.zwo")
                .classes("hidden")
            )
            ui.button(
                "Import .zwo", icon="upload_file", on_click=lambda: uploader.run_method("pickFiles")
            ).props("color=secondary")

    async def load(self) -> None:
        """Load planned workouts from the connected services into the listing."""
        e = self.e
        self.listing.clear()
        sources = e.sync.connected(Capability.WORKOUTS)
        if not sources:
            names = " or ".join(c.name for c in e.sync.with_capability(Capability.WORKOUTS))
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
                    S.settings.ftp,
                    S.settings.power_zones,
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
                workout_card(p.workout, label)


@ui.page("/workouts")
def workouts_page() -> None:
    """Workouts page: planned workouts from the connections, built-ins and .zwo import."""
    frame("Workouts")
    WorkoutsPage(S.engine)


# ================================================================== DEVICES
CALIBRATION_STEPS = {
    CalibrationKind.SPIN_DOWN: [
        "Warm up the trainer for about 10 minutes first.",
        "Press Start, then pedal up to the target speed shown below.",
        "When told to, stop pedalling completely and let the flywheel coast to a stop. "
        "Don't touch the pedals until it's done.",
    ],
    CalibrationKind.ZERO_OFFSET: [
        "Wake the power meter by turning the cranks a few times.",
        "Unclip and put the cranks vertical (one pedal at 6 o'clock).",
        "Keep the bike still, with no weight on the pedals, then press Start.",
    ],
}
PHASE_LABEL = {
    CalibrationPhase.STARTING: "Starting…",
    CalibrationPhase.SPEED_UP: "Speed up",
    CalibrationPhase.COAST: "Stop pedalling",
    CalibrationPhase.MEASURING: "Measuring",
}


USE_LABEL = {
    Role.TRAINER: "Use as trainer",
    Role.POWER: "Use for power",
    Role.HR: "Use for HR",
}


def roles_for(role: Role, settings: Settings) -> list[Role]:
    """Roles to give a device the rider picked for ``role``.

    A power meter also provides cadence; a trainer provides speed, and power and cadence
    too while no power source is assigned.

    Args:
        role (Role): The role the rider picked.
        settings (Settings): User settings (for the current role assignment).

    Returns:
        list[Role]: The roles to assign.
    """
    roles = [role]
    if role == Role.POWER:
        roles.append(Role.CADENCE)
    if role == Role.TRAINER and Role.POWER.value not in settings.role_assignment:
        roles += [Role.POWER, Role.CADENCE]
    if role == Role.TRAINER:
        roles.append(Role.SPEED)
    return roles


class CalibrationDialog:
    """Guided calibration of one device, rendered into a (reused) dialog.

    Attributes:
        e (Engine): The application engine.
        device_id (str): Id of the device to calibrate.
        task (asyncio.Task | None): The running (or last) calibration.
    """

    def __init__(
        self, e: Engine, dialog: ui.dialog, device_id: str, on_change: Callable[[], None]
    ) -> None:
        """Prepare the dialog; :meth:`open` renders and shows it.

        Args:
            e (Engine): The application engine.
            dialog (ui.dialog): Dialog to render into (cleared on open).
            device_id (str): Id of the device to calibrate.
            on_change (Callable[[], None]): Called when a calibration starts, to redraw
                the device list.
        """
        self.e = e
        self.dialog = dialog
        self.device_id = device_id
        self.task: asyncio.Task | None = None
        self._on_change = on_change

    def open(self) -> None:
        """Render the guided calibration and open the dialog (calibratable devices only)."""
        dev = self.e.devices.get(self.device_id)
        if not isinstance(dev, Calibratable):
            return
        dialog = self.dialog
        dialog.clear()
        with dialog, ui.card().classes("w-[32rem] max-w-full"):
            spin_down = dev.CALIBRATION == CalibrationKind.SPIN_DOWN
            title = "Spin-down calibration" if spin_down else "Zero-offset calibration"
            ui.label(f"{title}: {dev.name}").classes("text-xl font-bold")
            with ui.column().classes("gap-1"):
                for i, text in enumerate(CALIBRATION_STEPS[dev.CALIBRATION], 1):
                    ui.label(f"{i}. {text}")
            with ui.column().classes("w-full gap-1") as self.live:
                self.phase_lbl = ui.label().classes("text-lg font-semibold")
                self.msg_lbl = ui.label()
                self.speed_lbl = ui.label().classes("text-3xl font-bold")
                self.bar = ui.linear_progress(value=0, show_value=False).props("size=12px rounded")
            self.live.set_visibility(False)
            self.result_lbl = ui.label().classes("text-base")
            with ui.row().classes("w-full justify-end"):
                self.start_btn = ui.button("Start", icon="play_arrow", on_click=self.start)
                self.cancel_btn = ui.button("Cancel", on_click=self.cancel).props("flat")
                self.close_btn = ui.button("Close", on_click=dialog.close).props("flat")
            self.cancel_btn.set_visibility(False)
        with dialog:
            ui.timer(0.3, self.update)
        dialog.open()

    def start(self) -> None:
        """Start the calibration in the background."""
        reason = self.e.calibration.blocker(self.device_id)
        if reason:
            ui.notify(reason, type="warning")
            return
        self.task = asyncio.create_task(self.e.calibration.run(self.device_id))
        self._on_change()

    def cancel(self) -> None:
        """Cancel a running calibration."""
        if self.task and not self.task.done():
            self.task.cancel()

    def update(self) -> None:
        """Render the latest calibration progress."""
        task = self.task
        running = task is not None and not task.done()
        self._update_buttons(running)
        p = self.e.calibration.progress
        if task is None or p is None:
            return
        self.live.set_visibility(running)
        if running:
            self._show_progress(p)
        elif p.phase == CalibrationPhase.DONE:
            self.result_lbl.set_text(f"✓ {p.message}")
            self.result_lbl.classes(replace="text-base text-positive font-semibold")
        else:
            self.result_lbl.set_text(f"✗ {p.message}")
            self.result_lbl.classes(replace="text-base text-negative font-semibold")

    def _update_buttons(self, running: bool) -> None:
        """Show start/cancel/close depending on the calibration state.

        Args:
            running (bool): Whether a calibration is running.
        """
        task = self.task
        self.start_btn.set_visibility(not running)
        if task is None:
            self.start_btn.set_text("Start")
        elif task.done():
            failed = task.cancelled() or task.exception() is not None
            self.start_btn.set_text("Try again" if failed else "Run again")
        self.cancel_btn.set_visibility(running)
        self.close_btn.set_visibility(not running)

    def _show_progress(self, p: CalibrationProgress) -> None:
        """Show the phase, instruction, speed and progress of a running calibration.

        Args:
            p (CalibrationProgress): Latest progress.
        """
        self.phase_lbl.set_text(PHASE_LABEL.get(p.phase, p.phase.value))
        self.msg_lbl.set_text(p.message)
        if p.speed_kmh is not None:
            target = (
                f" / target {p.target_low_kmh:.0f}-{p.target_high_kmh:.0f} km/h"
                if p.target_low_kmh and p.target_high_kmh
                else ""
            )
            self.speed_lbl.set_text(f"{p.speed_kmh:.1f} km/h{target}")
        else:
            self.speed_lbl.set_text("")
        if p.fraction is None:
            self.bar.props("indeterminate")
        else:
            self.bar.props(remove="indeterminate")
            self.bar.set_value(p.fraction)
        self.result_lbl.set_text("")


class DevicesPage:
    """Devices page widgets: paired devices and BLE scanning/pairing.

    Attributes:
        e (Engine): The application engine.
    """

    def __init__(self, e: Engine) -> None:
        """Build the page and refresh the paired devices every 2 s.

        Args:
            e (Engine): The application engine.
        """
        self.e = e
        with ui.column().classes("page page-narrow"):
            ui.label("Paired devices").classes("section-title")
            self.paired = ui.column().classes("w-full gap-2")
            with ui.row().classes("items-center gap-2 w-full mt-4"):
                ui.label("Find devices").classes("section-title")
                ui.space()
                self.scan_btn = ui.button("Scan", icon="bluetooth_searching", on_click=self.do_scan)
            ui.label(
                "Wake devices up first: pedal to wake the trainer and the Assioma pedals, "
                "and put on the HRM strap."
            ).classes("hint")
            self.found = ui.column().classes("w-full gap-2")
        # Created at page level: ``render_paired`` clears the device cards every 2 s.
        self.cal_dialog = ui.dialog().props("persistent")
        self.render_paired()
        ui.timer(2.0, self.render_paired)

    # ----------------------------------------------------------- paired
    def render_paired(self) -> None:
        """Redraw the list of paired devices."""
        e = self.e
        self.paired.clear()
        roles_by_dev: dict[str, list[str]] = {}
        for role, did in e.devices.assignment.roles.items():
            roles_by_dev.setdefault(did, []).append(role.value)
        with self.paired:
            if not e.devices:
                ui.label("No devices yet: scan below.").classes("hint")
            for did, dev in e.devices.items():
                self._paired_card(did, dev, ", ".join(sorted(roles_by_dev.get(did, []))) or "-")

    def _paired_card(self, did: str, dev: Device, used_for: str) -> None:
        """Render one paired device with its calibrate and forget buttons.

        Args:
            did (str): Device id.
            dev (Device): The device.
            used_for (str): Roles the device is assigned to, for display.
        """
        with ui.card().classes("w-full panel"), ui.row().classes("items-center w-full"):
            ui.element("span").classes("dev-dot").style(
                f"background: {DOT_COLOR[dev.state]}"
            ).tooltip(str(dev.state))
            with ui.column().classes("gap-0"):
                ui.label(dev.name).classes("list-title")
                ui.label(f"{dev.DRIVER} · used for {used_for}").classes("list-sub")
            ui.space()
            if isinstance(dev, Calibratable):
                blocker = self.e.calibration.blocker(did)
                cal_btn = ui.button(
                    "Calibrate", icon="tune", on_click=lambda: self.open_calibration(did)
                ).props("flat")
                if blocker:
                    cal_btn.disable()
                    cal_btn.tooltip(blocker)
            ui.button(icon="link_off", on_click=lambda: self.remove(did)).props("flat").tooltip(
                "Forget"
            )

    async def remove(self, device_id: str) -> None:
        """Forget a paired device.

        Args:
            device_id (str): Id of the device to remove.
        """
        await self.e.devices.remove(device_id)
        self.render_paired()

    def open_calibration(self, device_id: str) -> None:
        """Open the guided calibration dialog for a device.

        Args:
            device_id (str): Id of the device to calibrate.
        """
        CalibrationDialog(self.e, self.cal_dialog, device_id, self.render_paired).open()

    # ------------------------------------------------------------- scan
    async def do_scan(self) -> None:
        """Scan for BLE devices and list them with role buttons."""
        from smart_trainer.ble.scanner import scan

        self.found.clear()
        self.scan_btn.props("loading")
        try:
            results = await scan(6.0)
        except Exception as exc:
            ui.notify(
                f"Bluetooth scan failed: {exc}. Check that Bluetooth is on and allowed "
                "for your terminal in System Settings → Privacy & Security → Bluetooth.",
                type="negative",
                multi_line=True,
            )
            return
        finally:
            self.scan_btn.props(remove="loading")
        with self.found:
            if not results:
                ui.label("Nothing found.").classes("hint")
            for r in results:
                self._found_card(r)

    def _found_card(self, r: Discovered) -> None:
        """Render one discovered device with a button per role it can take.

        Args:
            r (Discovered): The discovered device.
        """
        with ui.card().classes("w-full panel"), ui.row().classes("items-center w-full"):
            with ui.column().classes("gap-0"):
                ui.label(r.name).classes("list-title")
                ui.label(f"{', '.join(sorted(r.roles))} · RSSI {r.rssi}").classes("list-sub")
            ui.space()
            driver = DRIVERS[r.drivers[0]]
            for role in USE_LABEL:
                if role in driver.ROLES:
                    ui.button(
                        USE_LABEL[role],
                        icon=ROLE_ICON[role],
                        on_click=lambda role=role: self.use(r, role),
                    ).props("color=secondary")

    def use(self, r: Discovered, role: Role) -> None:
        """Assign a discovered device to a role, pairing it if needed.

        Args:
            r (Discovered): The discovered device.
            role (Role): The role to assign.
        """
        from smart_trainer.ble.scanner import make_device

        e = self.e
        dev = e.devices.get(r.address) or make_device(r.drivers[0], r.address, r.name)
        roles = roles_for(role, S.settings)
        if dev.id in e.devices:
            e.devices.assign(dev.id, roles)
        else:
            e.devices.pair(dev, roles)
        ui.notify(f"{r.name} assigned to {role.value}")
        self.render_paired()


@ui.page("/devices")
def devices_page() -> None:
    """Devices page: paired devices and BLE scanning/pairing."""
    frame("Devices")
    DevicesPage(S.engine)


# ================================================================== HISTORY
def upload_chip(name: str, state: UploadState | None) -> None:
    """Show the upload state of a session on one connection.

    Args:
        name (str): Connection name.
        state (UploadState | None): Upload outcome, ``None`` if never uploaded.
    """
    if state and state.done:
        icon, color, tip = "cloud_done", "positive", f"Uploaded to {name} ({state.remote_id})"
    elif state and state.error:
        icon, color, tip = "error", "negative", f"{name} upload failed: {state.error}"
    else:
        icon, color, tip = "cloud_off", "grey", f"Not uploaded to {name}"
    with ui.row().classes("items-center gap-1 no-wrap"):
        ui.icon(icon, color=color, size="xs")
        ui.label(name).classes("list-sub")
        ui.tooltip(tip)


def session_icon(s: SessionInfo) -> tuple[str, str]:
    """Icon and color for a session's status.

    Args:
        s (SessionInfo): The session.

    Returns:
        tuple[str, str]: Material icon name and color.
    """
    if s.status == "finished":
        done = any(u.done for u in s.uploads.values())
        return ("cloud_done", "positive") if done else ("save", "primary")
    if s.status == "active":
        return "warning", "warning"
    return "help", "grey"


class HistoryPage:
    """History page widgets: recorded sessions and manual upload.

    Attributes:
        e (Engine): The application engine.
    """

    PAGE_SIZE = 30

    def __init__(self, e: Engine) -> None:
        """Build the page and schedule loading the sessions.

        Args:
            e (Engine): The application engine.
        """
        self.e = e
        self.limit = self.PAGE_SIZE
        with ui.column().classes("page page-narrow"):
            ui.label("Sessions").classes("section-title")
            ui.label(f"Stored in {e.store.directory}").classes("hint")
            self.listing = ui.column().classes("w-full gap-2")
            self.more = (
                ui.button("Load more", icon="expand_more", on_click=self.load_more)
                .props("flat color=secondary")
                .mark("history-more")
            )
            self.more.set_visibility(False)
        ui.timer(0, self.render, once=True)

    async def render(self) -> None:
        """Redraw the list of recorded sessions (read in a worker thread)."""
        e = self.e
        # one extra to know whether there's more to load
        sessions = await asyncio.to_thread(e.store.list_sessions, self.limit + 1)
        self.listing.clear()
        uploaders = e.sync.with_capability(Capability.UPLOAD)
        with self.listing:
            if not sessions:
                ui.label("No sessions recorded yet.").classes("hint")
            for s in sessions[: self.limit]:
                self._session_card(s, uploaders)
        self.more.set_visibility(len(sessions) > self.limit)

    async def load_more(self) -> None:
        """Show the next page of older sessions."""
        self.limit += self.PAGE_SIZE
        await self.render()

    def _session_card(self, s: SessionInfo, uploaders: list[Connection]) -> None:
        """Render one session with its upload state and, if needed, an upload button.

        Args:
            s (SessionInfo): The session.
            uploaders (list[Connection]): Connections that can upload rides.
        """
        names = {c.id: c.name for c in uploaders}
        with ui.card().classes("w-full panel"), ui.row().classes("items-center w-full"):
            icon, color = session_icon(s)
            ui.icon(icon, color=color, size="sm")
            with ui.column().classes("gap-0"):
                ui.label(s.name).classes("list-title")
                when = f"{s.started:%a %d %b %Y %H:%M}"
                ui.label(f"{when} · {fmt_time(s.elapsed_s)} · {s.status}").classes("list-sub")
                shown = [c.id for c in uploaders if c.usable or c.id in s.uploads]
                shown += [cid for cid in s.uploads if cid not in shown]
                if s.status == "finished" and shown:
                    with ui.row().classes("gap-3"):
                        for cid in shown:
                            upload_chip(names.get(cid, cid), s.uploads.get(cid))
            ui.space()
            pending = [c.name for c in uploaders if c.usable and not s.uploaded(c.id)]
            if s.status == "finished" and s.path.with_suffix(".fit").exists() and pending:
                ui.button("Upload", icon="cloud_upload", on_click=lambda: self.upload(s)).props(
                    "color=secondary"
                ).tooltip("Upload to " + ", ".join(pending))

    async def upload(self, s: SessionInfo) -> None:
        """Upload a finished session where it's still missing.

        Args:
            s (SessionInfo): The session to upload.
        """
        e = self.e
        seen = len(e.messages)
        result = await e.store.upload_session(s)
        ok = bool(result) and all(result.values())
        for m in list(e.messages)[seen:]:
            ui.notify(m, type="positive" if ok else "negative")
        await self.render()


@ui.page("/history")
def history_page() -> None:
    """History page: recorded sessions and manual upload."""
    frame("History")
    HistoryPage(S.engine)


# ================================================================= SETTINGS
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
        e (Engine): The application engine.
        st (Settings): User settings edited by the page.
    """

    def __init__(self, e: Engine) -> None:
        """Build the page.

        Args:
            e (Engine): The application engine.
        """
        self.e = e
        self.st = S.settings
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
        profile_sources = self.e.sync.with_capability(Capability.PROFILE)
        source = " or ".join(c.name for c in profile_sources) or "your training platform"
        ui.label("Rider profile").classes("section-title")
        self.sync = ui.switch(f"Sync FTP, zones and weight from {source}", value=st.sync_profile)
        self.ftp = ui.number("FTP (W)", value=st.ftp, min=50, max=600, step=1)
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
        self.max_off = ui.number("Max correction (W)", value=pm.max_offset_w, min=0, max=150)
        self.settle = ui.number(
            "Settle time after target change (s)", value=pm.settle_s, min=0, max=30
        )
        ui.button("Save", icon="save", on_click=self.save).props("color=positive").classes("mt-2")

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
        for conn in self.e.sync.connections():
            ui.separator()
            connection_card(conn, self.credentials_changed)

    # ---------------------------------------------------------- profile
    def refresh_profile(self) -> None:
        """Show the current profile and lock the fields while syncing is on."""
        st = self.st
        self.ftp.value, self.weight.value = st.ftp, st.weight_kg
        on = bool(self.sync.value)
        for field in (self.ftp, self.weight):
            if on:
                field.props("readonly")
            else:
                field.props(remove="readonly")
        self.sync_btn.set_visibility(on)
        self.summary.set_visibility(on)
        self.summary.set_text(profile_summary(st))

    async def sync_now(self) -> None:
        """Fetch the profile and show the result."""
        e = self.e
        self.sync_btn.props("loading")
        try:
            changed = await e.sync.sync_profile()
        finally:
            self.sync_btn.props(remove="loading")
        if changed is None:
            ui.notify(e.messages[-1] if e.messages else "Sync failed", type="negative")
        else:
            text = f"Updated {', '.join(changed)}" if changed else "Already up to date"
            ui.notify(text, type="positive")
        self.refresh_profile()

    def can_sync(self) -> bool:
        """Whether a profile connection is usable.

        Returns:
            bool: ``True`` if the profile can be synced.
        """
        return bool(self.e.sync.connected(Capability.PROFILE))

    async def toggle_sync(self) -> None:
        """Persist the sync switch; sync right away when it is turned on."""
        st = self.st
        st.sync_profile = bool(self.sync.value)
        st.save()
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
        st = self.st
        pm = st.power_match
        if not st.sync_profile:  # synced values come from the profile connection only
            st.ftp = int(self.ftp.value) if self.ftp.value is not None else st.ftp
            st.weight_kg = (
                float(self.weight.value) if self.weight.value is not None else st.weight_kg
            )
        if self.max_off.value is not None:
            pm.max_offset_w = float(self.max_off.value)
        if self.settle.value is not None:
            pm.settle_s = float(self.settle.value)
        st.save()
        ui.notify("Settings saved", type="positive")

    def toggle_cues(self) -> None:
        """Persist the interval cues switch."""
        self.st.interval_cues = bool(self.cues.value)
        self.st.save()

    def toggle_spiral_guard(self, on: bool) -> None:
        """Persist the ERG spiral guard switch and apply it to the ride in progress.

        Args:
            on (bool): Whether the guard is on.
        """
        self.st.erg_spiral_guard = on
        self.st.save()
        self.e.controller.guard = SpiralGuard() if on else None

    def toggle_auto(self) -> None:
        """Persist the global automatic upload switch."""
        self.st.auto_upload = bool(self.auto.value)
        self.st.save()


@ui.page("/settings")
def settings_page() -> None:
    """Settings page: rider profile, connections and power matching."""
    frame("Settings")
    SettingsPage(S.engine)
