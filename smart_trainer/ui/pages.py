"""NiceGUI pages. The UI only reads Engine state and calls Engine methods."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from nicegui import ui

from smart_trainer.ble.device import (
    DRIVERS,
    Calibratable,
    CalibrationKind,
    CalibrationPhase,
)
from smart_trainer.config import Settings, get_api_key, set_api_key
from smart_trainer.core.control import Mode
from smart_trainer.core.engine import Engine
from smart_trainer.core.library import builtin_workouts
from smart_trainer.core.recorder import list_sessions
from smart_trainer.core.ride import HistoryPoint, RideState
from smart_trainer.core.state import ConnState, Role
from smart_trainer.core.workout import Workout, parse_zwo
from smart_trainer.ui.theme import apply_theme
from smart_trainer.ui.widgets import (
    Tile,
    fmt,
    fmt_time,
    hr_zone,
    power_zone,
    workout_chart,
)

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


S = AppState()

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
        chips = ui.row().classes("gap-2")

    def refresh_chips() -> None:
        """Redraw the device status chips."""
        chips.clear()
        e = S.engine
        with chips:
            for role in (Role.TRAINER, Role.POWER, Role.HR):
                dev = e.devices.device_for(role)
                color = DOT_COLOR[dev.state] if dev else DOT_COLOR[None]
                tip = (
                    f"{role.value}: {dev.name} ({dev.state})"
                    if dev
                    else f"{role.value}: not paired"
                )
                with ui.element("div").classes("dev-chip") as chip:
                    ui.icon(ROLE_ICON[role], size="16px")
                    ui.element("span").classes("dev-dot").style(f"background: {color}")
                chip.tooltip(tip)

    refresh_chips()
    ui.timer(2.0, refresh_chips)


# ===================================================================== RIDE
@ui.page("/")
def ride_page() -> None:
    """Ride page: live metrics, workout chart and ride controls."""
    e = S.engine
    frame("Ride")
    ftp = S.settings.ftp

    with ui.column().classes("page"):
        warn = ui.label("").classes("status-bar")
        with ui.element("div").classes("metric-grid"):
            t_power = Tile("Power · 3s", "W", icon="bolt", size="lg", accent="#facc15")
            t_target = Tile("Target", "W", icon="adjust", size="lg", accent="#4f8cff")
            t_hr = Tile("Heart rate", "bpm", icon="favorite", size="lg", accent="#f43f5e")
            t_cad = Tile("Cadence", "rpm", icon="autorenew", size="lg", accent="#a78bfa")
        with ui.element("div").classes("metric-grid"):
            t_time = Tile("Elapsed", icon="schedule")
            t_step = Tile("Interval left", icon="timer")
            t_speed = Tile("Speed", "km/h", icon="speed")
            t_bal = Tile("Balance", "L/R", icon="balance")

        with ui.card().classes("w-full panel gap-2") as step_card:
            with ui.row().classes("w-full items-baseline justify-between no-wrap gap-4"):
                step_label = ui.label("").classes("step-title")
                next_label = ui.label("").classes("step-next text-right")
            step_bar = (
                ui.linear_progress(value=0, show_value=False)
                .props("rounded size=6px color=primary instant-feedback")
                .classes("step-progress")
            )

        holder = ui.card().classes("w-full p-2")
        chart_ref: dict = {"chart": None, "workout": object()}

        def ensure_chart() -> None:
            """Recreate the chart when the displayed workout changes."""
            wo = (
                e.ride.runner.workout
                if e.ride.runner
                else (S.selected if e.ride.state == RideState.IDLE else None)
            )
            if chart_ref["workout"] is wo:
                return
            holder.clear()
            with holder:
                chart_ref["chart"] = workout_chart(wo, ftp)
            chart_ref["workout"] = wo

        # ---------------------------------------------------------- controls
        with ui.card().classes("w-full panel"), ui.row().classes("items-center gap-3 w-full"):
            btn_start = ui.button("Start", icon="play_arrow", on_click=lambda: start()).props(
                "color=positive size=lg padding='8px 28px'"
            )
            btn_pause = ui.button(
                "Pause", icon="pause", on_click=lambda: e.ride.toggle_pause()
            ).props("color=primary size=lg padding='8px 24px'")
            btn_skip = ui.button(
                "Skip", icon="skip_next", on_click=lambda: e.ride.skip_step()
            ).props("color=secondary")
            btn_lap = ui.button("Lap", icon="flag", on_click=lambda: e.ride.lap()).props(
                "color=secondary"
            )
            with ui.row().classes("items-center gap-1 ml-2") as intensity_box:
                ui.label("Intensity").classes("field-label mr-1")
                ui.button(icon="remove", on_click=lambda: e.ride.change_intensity(-0.05)).props(
                    "color=secondary round dense"
                ).tooltip("Intensity -5%")
                intensity = ui.label("100%").classes("control-value")
                ui.button(icon="add", on_click=lambda: e.ride.change_intensity(0.05)).props(
                    "color=secondary round dense"
                ).tooltip("Intensity +5%")
            erg = ui.switch(
                "ERG", value=e.ride.erg_enabled, on_change=lambda ev: e.ride.set_erg(ev.value)
            )
            ui.space()
            btn_finish = ui.button("Finish", icon="stop", on_click=lambda: confirm_finish()).props(
                "color=negative outline size=lg padding='8px 24px'"
            )

        with ui.card().classes("w-full panel"), ui.row().classes("items-center gap-6 w-full"):
            with ui.row().classes("items-center gap-4 grow no-wrap") as manual_box:
                ui.label("Manual").classes("field-label")
                mode = ui.toggle(
                    {"resistance": "Resistance", "slope": "Slope", "erg": "Fixed ERG"},
                    value=e.controller.mode.value if e.ride.runner is None else "resistance",
                    on_change=lambda ev: set_manual_mode(ev.value),
                ).classes("shrink-0")
                slider = ui.slider(
                    min=0,
                    max=100,
                    step=1,
                    value=e.controller.resistance_pct,
                    on_change=lambda ev: set_manual_value(ev.value),
                ).classes("grow min-w-[120px]")
                slider_label = ui.label("").classes("control-value w-24")
            with ui.row().classes("items-center gap-3 shrink-0"):
                ui.label("Power source").classes("field-label")
                ui.toggle(
                    {"pedals": "Pedals", "trainer": "Trainer"},
                    value=S.settings.power_source,
                    on_change=lambda ev: e.devices.set_power_source(ev.value),
                )

    def set_manual_mode(m: str) -> None:
        """Switch the manual control mode and adapt the slider range.

        Args:
            m (str): Mode value (``"resistance"``, ``"slope"`` or ``"erg"``).
        """
        c = e.controller
        c.set_mode(Mode(m))
        if m == "resistance":
            slider.props("min=0 max=100 step=1")
            slider.value = c.resistance_pct
        elif m == "slope":
            slider.props("min=-10 max=20 step=0.5")
            slider.value = c.slope_pct
        else:
            slider.props("min=50 max=600 step=5")
            slider.value = c.erg_target

    def set_manual_value(v: float) -> None:
        """Apply the slider value to the current manual mode.

        Args:
            v (float): Slider value (percent, grade or watts depending on mode).
        """
        c = e.controller
        if c.mode == Mode.RESISTANCE:
            c.resistance_pct = v
        elif c.mode == Mode.SLOPE:
            c.slope_pct = v
        else:
            c.erg_target = v

    def start() -> None:
        """Start a ride with the selected workout (or a free ride)."""
        try:
            e.ride.start(S.selected)
        except RuntimeError as exc:
            ui.notify(str(exc), type="warning")
            return
        ui.notify(f"Started: {S.selected.name if S.selected else 'free ride'}")

    async def do_finish(dialog, upload: bool) -> None:
        """Finish the ride and notify the result.

        Args:
            dialog (ui.dialog): Confirmation dialog to close.
            upload (bool): Whether to upload the ride to intervals.icu.
        """
        dialog.close()
        n = ui.notification("Saving session…", spinner=True, timeout=None)
        fit = await e.ride.finish(upload=upload)
        n.dismiss()
        S.selected = None
        ui.notify(f"Saved {fit.name}" if fit else "Nothing recorded", type="positive")
        for m in list(e.messages)[-1:]:
            ui.notify(m)

    def confirm_finish() -> None:
        """Ask the rider how to finish the session."""
        with ui.dialog() as d, ui.card():
            ui.label("Finish this session?").classes("text-lg")
            with ui.row():
                ui.button(
                    "Save & upload", icon="cloud_upload", on_click=lambda: do_finish(d, True)
                ).props("color=positive")
                ui.button("Save only", icon="save", on_click=lambda: do_finish(d, False))
                ui.button("Keep riding", on_click=d.close).props("flat")
        d.open()

    # ------------------------------------------------------------ recovery
    def recovery_dialog() -> None:
        """Offer to resume, finish or discard an unfinished session."""
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

    recovery_dialog()

    # ------------------------------------------------------------- refresh
    def refresh() -> None:
        """Update tiles, labels, buttons and controls from the engine state."""
        ensure_chart()
        snap = e.snapshot
        riding = e.ride.state == RideState.RIDING
        pedal_id = e.selector.assignment.get(Role.POWER)
        p3 = e.selector.avg_power(snap.power_source) if snap.power_source else None
        src = (
            "pedals"
            if snap.power_source
            and snap.power_source == pedal_id
            and snap.power_source != e.selector.trainer_id
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
        r = e.ride.runner
        st = r.current()[1] if r else None
        t_cad.set(fmt(snap.cadence), f"target {st.cadence} rpm" if st and st.cadence else "")
        t_speed.set(fmt(snap.speed_kmh, 1))
        t_bal.set(
            f"{snap.balance_left:.0f}/{100 - snap.balance_left:.0f}" if snap.balance_left else "--"
        )
        if r:
            i, st, into, left = r.current()
            t_time.set(fmt_time(r.elapsed), f"of {fmt_time(r.workout.duration_s)}")
            t_step.set(fmt_time(left), f"step {i + 1} of {len(r.workout.steps)}")
            label = st.text or ("Free ride" if st.free_ride else f"Interval {i + 1}")
            step_label.set_text(label)
            nxt = r.next_step()
            nt = nxt.target_w(0, r.ftp) if nxt else None
            next_label.set_text(
                f"Next · {nxt.text + ' · ' if nxt.text else ''}{fmt(nt)} W"
                f" for {fmt_time(nxt.duration_s)}"
                if nxt
                else "Last interval"
            )
            step_bar.set_value(min(1.0, into / st.duration_s) if st.duration_s else 0)
            intensity.set_text(f"{r.intensity * 100:.0f}%")
            step_card.set_visibility(True)
        else:
            t_time.set(fmt_time(e.ride.seconds if riding else 0), "free ride" if riding else "")
            t_step.set("--")
            sel = None if riding else S.selected
            step_label.set_text(sel.name if sel else "")
            next_label.set_text(
                f"{fmt_time(sel.duration_s)} · {len(sel.steps)} steps" if sel else ""
            )
            step_bar.set_value(0)
            step_card.set_visibility(sel is not None)
        status = []
        if e.ride.manual_pause:
            status.append("PAUSED")
        elif riding and e.ride.auto_paused:
            status.append("Auto-paused: start pedalling")
        status += snap.warnings
        if e.devices.device_for(Role.TRAINER) and not e.devices.trainer_connected():
            status.append("Trainer disconnected: reconnecting…")
        warn.set_text(" · ".join(status))
        warn.set_visibility(bool(status))
        btn_start.set_visibility(not riding)
        for b in (btn_pause, btn_finish, btn_lap):
            b.set_visibility(riding)
        btn_skip.set_visibility(riding and r is not None)
        btn_pause.set_text("Resume" if e.ride.manual_pause else "Pause")
        erg.set_visibility(r is not None)
        intensity_box.set_visibility(r is not None)
        manual_box.set_visibility(not (r and e.ride.erg_enabled))
        c = e.controller
        unit = {"resistance": "%", "slope": "% grade", "erg": " W"}[c.mode.value]
        val = {"resistance": c.resistance_pct, "slope": c.slope_pct, "erg": c.erg_target}[
            c.mode.value
        ]
        slider_label.set_text(f"{val:g}{unit}")
        if mode.value != c.mode.value and not (r and e.ride.erg_enabled):
            mode.value = c.mode.value

    tick = {"n": 0}

    def refresh_chart() -> None:
        """Update the live power and heart-rate series of the chart."""

        ch = chart_ref["chart"]
        if ch is None or not e.ride.history:
            return
        hist = list(e.ride.history)
        step = max(1, len(hist) // 1500)
        # x = workout elapsed: approximate with recorded active seconds
        offset = e.ride.runner.elapsed - hist[-1].t if e.ride.runner is not None else 0.0

        def x_of(p: HistoryPoint) -> float:
            """Chart x coordinate of a history point.

            Args:
                p (HistoryPoint): The history point.

            Returns:
                float: The x coordinate in seconds.
            """
            return p.t + offset

        ch.options["series"][1]["data"] = [
            [round(x_of(p)), p.power] for p in hist[::step] if p.power is not None
        ]
        ch.options["series"][2]["data"] = [[round(x_of(p)), p.hr] for p in hist[::step] if p.hr]
        if e.ride.runner is None:
            end = max(600, hist[-1].t)
            ch.options["xAxis"]["max"] = end
            ch.options["xAxis"]["min"] = max(0, end - 1200)
            ch.options["series"][0]["data"] = []
        ch.update()

    def loop() -> None:
        """Refresh the page, and the chart every other call."""
        refresh()
        tick["n"] += 1
        if tick["n"] % 2 == 0:
            refresh_chart()

    ui.timer(0.5, loop)


# ================================================================= WORKOUTS
@ui.page("/workouts")
def workouts_page() -> None:
    """Workouts page: planned intervals.icu workouts, built-ins and .zwo import."""
    e = S.engine
    frame("Workouts")
    with ui.column().classes("page"):
        if S.selected:
            with ui.row().classes("items-center gap-2"):
                ui.icon("check_circle", color="positive")
                ui.label(f"Selected: {S.selected.name}").classes("list-title")
        with ui.row().classes("items-center gap-2 w-full"):
            ui.label("Planned on intervals.icu").classes("section-title")
            ui.space()
            ui.button("Refresh", icon="refresh", on_click=lambda: load()).props("color=secondary")
            ui.button("Free ride", icon="all_inclusive", on_click=lambda: choose(None)).props(
                "color=secondary"
            )
            uploader = (
                ui.upload(auto_upload=True, on_upload=lambda ev: import_zwo(ev))
                .props("accept=.zwo")
                .classes("hidden")
            )
            ui.button(
                "Import .zwo", icon="upload_file", on_click=lambda: uploader.run_method("pickFiles")
            ).props("color=secondary")
        status = ui.label("").classes("hint")
        listing = ui.column().classes("w-full gap-3")
        ui.label("Built-in workouts").classes("section-title mt-4")
        builtin = ui.column().classes("w-full gap-3")

    def choose(wo: Workout | None) -> None:
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
        choose(wo)

    def card(wo: Workout, subtitle: str, where=None) -> None:
        """Render a workout card with its profile and a "Ride this" button.

        Args:
            wo (Workout): The workout to show.
            subtitle (str): Text shown under the workout name.
            where (ui.element | None): Container to render into; defaults to the
                intervals.icu listing.
        """
        with where or listing, ui.card().classes("w-full panel gap-2"):
            with ui.row().classes("items-center w-full"):
                with ui.column().classes("gap-0"):
                    ui.label(wo.name).classes("list-title")
                    ui.label(
                        f"{subtitle} · {fmt_time(wo.duration_s)} · {len(wo.steps)} steps"
                    ).classes("list-sub")
                ui.space()
                ui.button("Ride this", icon="play_arrow", on_click=lambda w=wo: choose(w)).props(
                    "color=positive"
                )
            workout_chart(wo, S.settings.ftp, height="h-28", live=False)

    async def load() -> None:
        """Load planned workouts from intervals.icu into the listing."""
        listing.clear()
        async with e.sync.provider() as provider:
            if provider is None:
                status.set_text(
                    "Add your intervals.icu API key in Settings to load planned workouts."
                )
                return
            status.set_text("Loading…")
            try:
                today = date.today()
                planned = await provider.list_workouts(
                    today - timedelta(days=2),
                    today + timedelta(days=7),
                    S.settings.ftp,
                    S.settings.power_zones,
                )
            except Exception as exc:
                status.set_text(f"Could not load workouts: {exc}")
                return
        status.set_text(
            f"{len(planned)} cycling workouts planned"
            if planned
            else "No cycling workouts planned in the next 7 days."
        )
        for p in planned:
            label = "Today" if p.day == date.today() else p.day.strftime("%a %d %b")
            card(p.workout, label)

    for wo in builtin_workouts():
        card(wo, wo.description, builtin)
    ui.timer(0.1, load, once=True)


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


@ui.page("/devices")
def devices_page() -> None:
    """Devices page: paired devices and BLE scanning/pairing."""
    e = S.engine
    frame("Devices")
    with ui.column().classes("page page-narrow"):
        ui.label("Paired devices").classes("section-title")
        paired = ui.column().classes("w-full gap-2")
        with ui.row().classes("items-center gap-2 w-full mt-4"):
            ui.label("Find devices").classes("section-title")
            ui.space()
            scan_btn = ui.button("Scan", icon="bluetooth_searching", on_click=lambda: do_scan())
        ui.label(
            "Wake devices up first: pedal to wake the trainer and the Assioma pedals, "
            "and put on the HRM strap."
        ).classes("hint")
        found = ui.column().classes("w-full gap-2")

    def render_paired() -> None:
        """Redraw the list of paired devices."""
        paired.clear()
        roles_by_dev: dict[str, list[str]] = {}
        for role, did in e.selector.assignment.items():
            roles_by_dev.setdefault(did, []).append(role.value)
        with paired:
            if not e.devices:
                ui.label("No devices yet: scan below.").classes("hint")
            for did, dev in e.devices.items():
                with ui.card().classes("w-full panel"), ui.row().classes("items-center w-full"):
                    ui.element("span").classes("dev-dot").style(
                        f"background: {DOT_COLOR[dev.state]}"
                    ).tooltip(str(dev.state))
                    with ui.column().classes("gap-0"):
                        ui.label(dev.name).classes("list-title")
                        used_for = ", ".join(sorted(roles_by_dev.get(did, []))) or "-"
                        ui.label(f"{dev.DRIVER} · used for {used_for}").classes("list-sub")
                    ui.space()
                    if isinstance(dev, Calibratable):
                        blocker = e.calibration.blocker(did)
                        cal_btn = ui.button(
                            "Calibrate", icon="tune", on_click=lambda d=did: open_calibration(d)
                        ).props("flat")
                        if blocker:
                            cal_btn.disable()
                            cal_btn.tooltip(blocker)

                    async def remove(d=did):
                        """Forget a paired device.

                        Args:
                            d (str): Id of the device to remove.
                        """
                        await e.devices.remove(d)
                        render_paired()

                    ui.button(icon="link_off", on_click=remove).props("flat").tooltip("Forget")

    async def do_scan() -> None:
        """Scan for BLE devices and list them with role buttons."""
        from smart_trainer.ble.scanner import scan

        found.clear()
        scan_btn.props("loading")
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
            scan_btn.props(remove="loading")
        with found:
            if not results:
                ui.label("Nothing found.").classes("hint")
            for r in results:
                with ui.card().classes("w-full panel"), ui.row().classes("items-center w-full"):
                    with ui.column().classes("gap-0"):
                        ui.label(r.name).classes("list-title")
                        ui.label(f"{', '.join(sorted(r.roles))} · RSSI {r.rssi}").classes(
                            "list-sub"
                        )
                    ui.space()
                    driver = DRIVERS[r.drivers[0]]
                    for role in (Role.TRAINER, Role.POWER, Role.HR):
                        if role in driver.ROLES:
                            label = {
                                "trainer": "Use as trainer",
                                "power": "Use for power",
                                "hr": "Use for HR",
                            }[role.value]
                            ui.button(
                                label,
                                icon=ROLE_ICON[role],
                                on_click=lambda r=r, role=role: use(r, role),
                            ).props("color=secondary")

    # Created at page level: ``render_paired`` clears the device cards every 2 s.
    cal_dialog = ui.dialog().props("persistent")
    cal_task: list[asyncio.Task | None] = [None]

    def open_calibration(device_id: str) -> None:
        """Open the guided calibration dialog for a device.

        Args:
            device_id (str): Id of the device to calibrate.
        """
        dev = e.devices.get(device_id)
        if not isinstance(dev, Calibratable):
            return
        spin_down = dev.CALIBRATION == CalibrationKind.SPIN_DOWN
        cal_task[0] = None
        cal_dialog.clear()
        with cal_dialog, ui.card().classes("w-[32rem] max-w-full"):
            title = "Spin-down calibration" if spin_down else "Zero-offset calibration"
            ui.label(f"{title}: {dev.name}").classes("text-xl font-bold")
            steps = CALIBRATION_STEPS[dev.CALIBRATION]
            with ui.column().classes("gap-1"):
                for i, text in enumerate(steps, 1):
                    ui.label(f"{i}. {text}")
            with ui.column().classes("w-full gap-1") as live:
                phase_lbl = ui.label().classes("text-lg font-semibold")
                msg_lbl = ui.label()
                speed_lbl = ui.label().classes("text-3xl font-bold")
                bar = ui.linear_progress(value=0, show_value=False).props("size=12px rounded")
            live.set_visibility(False)
            result_lbl = ui.label().classes("text-base")
            with ui.row().classes("w-full justify-end"):
                start_btn = ui.button("Start", icon="play_arrow", on_click=lambda: start())
                cancel_btn = ui.button("Cancel", on_click=lambda: cancel()).props("flat")
                close_btn = ui.button("Close", on_click=cal_dialog.close).props("flat")
            cancel_btn.set_visibility(False)

        def start() -> None:
            """Start the calibration in the background."""
            reason = e.calibration.blocker(device_id)
            if reason:
                ui.notify(reason, type="warning")
                return
            cal_task[0] = asyncio.create_task(e.calibration.run(device_id))
            render_paired()

        def cancel() -> None:
            """Cancel a running calibration."""
            if cal_task[0] and not cal_task[0].done():
                cal_task[0].cancel()

        def update() -> None:
            """Render the latest calibration progress."""
            task = cal_task[0]
            running = task is not None and not task.done()
            start_btn.set_visibility(task is None or task.done())
            if task is None:
                start_btn.set_text("Start")
            elif task.done():
                failed = task.cancelled() or task.exception() is not None
                start_btn.set_text("Try again" if failed else "Run again")
            cancel_btn.set_visibility(running)
            close_btn.set_visibility(not running)
            p = e.calibration.progress
            if task is None or p is None:
                return
            live.set_visibility(running)
            if running:
                phase_lbl.set_text(PHASE_LABEL.get(p.phase, p.phase.value))
                msg_lbl.set_text(p.message)
                if p.speed_kmh is not None:
                    target = (
                        f" / target {p.target_low_kmh:.0f}-{p.target_high_kmh:.0f} km/h"
                        if p.target_low_kmh and p.target_high_kmh
                        else ""
                    )
                    speed_lbl.set_text(f"{p.speed_kmh:.1f} km/h{target}")
                else:
                    speed_lbl.set_text("")
                if p.fraction is None:
                    bar.props("indeterminate")
                else:
                    bar.props(remove="indeterminate")
                    bar.set_value(p.fraction)
                result_lbl.set_text("")
            elif p.phase == CalibrationPhase.DONE:
                result_lbl.set_text(f"✓ {p.message}")
                result_lbl.classes(replace="text-base text-positive font-semibold")
            else:
                result_lbl.set_text(f"✗ {p.message}")
                result_lbl.classes(replace="text-base text-negative font-semibold")

        with cal_dialog:
            ui.timer(0.3, update)
        cal_dialog.open()

    def use(r, role: Role) -> None:
        """Assign a discovered device to a role, pairing it if needed.

        Args:
            r (Discovered): The discovered device.
            role (Role): The role to assign.
        """
        from smart_trainer.ble.scanner import make_device

        dev = e.devices.get(r.address) or make_device(r.drivers[0], r.address, r.name)
        roles = [role]
        if role == Role.POWER:
            roles.append(Role.CADENCE)
        if role == Role.TRAINER and Role.POWER.value not in S.settings.role_assignment:
            roles += [Role.POWER, Role.CADENCE]
        if role == Role.TRAINER:
            roles.append(Role.SPEED)
        if dev.id in e.devices:
            e.devices.assign(dev.id, roles)
        else:
            e.devices.pair(dev, roles)
        ui.notify(f"{r.name} assigned to {role.value}")
        render_paired()

    render_paired()
    ui.timer(2.0, render_paired)


# ================================================================== HISTORY
@ui.page("/history")
def history_page() -> None:
    """History page: recorded sessions and manual upload."""
    e = S.engine
    frame("History")
    with ui.column().classes("page page-narrow"):
        ui.label("Sessions").classes("section-title")
        ui.label(f"Stored in {e.store.directory}").classes("hint")
        listing = ui.column().classes("w-full gap-2")

    def render() -> None:
        """Redraw the list of recorded sessions."""
        listing.clear()
        with listing:
            for s in list_sessions(e.store.directory):
                with ui.card().classes("w-full panel"), ui.row().classes("items-center w-full"):
                    icon, color = {
                        "uploaded": ("cloud_done", "positive"),
                        "finished": ("save", "primary"),
                        "active": ("warning", "warning"),
                    }.get(s.status, ("help", "grey"))
                    ui.icon(icon, color=color, size="sm")
                    with ui.column().classes("gap-0"):
                        ui.label(s.name).classes("list-title")
                        extra = f" · upload error: {s.upload_error}" if s.upload_error else ""
                        when = f"{s.started:%a %d %b %Y %H:%M}"
                        ui.label(f"{when} · {fmt_time(s.elapsed_s)} · {s.status}{extra}").classes(
                            "list-sub"
                        )
                    ui.space()
                    fit = s.path.with_suffix(".fit")
                    if s.status == "finished" and fit.exists():

                        async def up(s=s):
                            """Upload a finished session to intervals.icu.

                            Args:
                                s (SessionInfo): The session to upload.
                            """
                            ok = await e.store.upload_session(s)
                            ui.notify(
                                "Uploaded" if ok else e.messages[-1],
                                type="positive" if ok else "negative",
                            )
                            render()

                        ui.button("Upload", icon="cloud_upload", on_click=up).props(
                            "color=secondary"
                        )

    render()


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


@ui.page("/settings")
def settings_page() -> None:
    """Settings page: rider profile, intervals.icu and power matching."""
    e = S.engine
    st = S.settings
    frame("Settings")
    with (
        ui.column().classes("page page-narrow [&_.q-field]:w-full"),
        ui.card().classes("w-full panel gap-3"),
    ):
        ui.label("Rider profile").classes("section-title")
        sync = ui.switch("Sync FTP, zones and weight from intervals.icu", value=st.sync_profile)
        ftp = ui.number("FTP (W)", value=st.ftp, min=50, max=600, step=1)
        weight = ui.number("Weight (kg)", value=st.weight_kg, min=30, max=200, step=0.5)
        summary = ui.label("").classes("hint whitespace-pre-line")
        sync_btn = ui.button("Sync now", icon="sync", on_click=lambda: sync_now()).props(
            "color=secondary"
        )

        def refresh_profile() -> None:
            """Show the current profile and lock the fields while syncing is on."""
            ftp.value, weight.value = st.ftp, st.weight_kg
            on = bool(sync.value)
            for field in (ftp, weight):
                if on:
                    field.props("readonly")
                else:
                    field.props(remove="readonly")
            sync_btn.set_visibility(on)
            summary.set_visibility(on)
            summary.set_text(profile_summary(st))

        async def sync_now() -> None:
            """Fetch the profile from intervals.icu and show the result."""
            sync_btn.props("loading")
            try:
                changed = await e.sync.sync_profile()
            finally:
                sync_btn.props(remove="loading")
            if changed is None:
                ui.notify(e.messages[-1] if e.messages else "Sync failed", type="negative")
            else:
                text = f"Updated {', '.join(changed)}" if changed else "Already up to date"
                ui.notify(text, type="positive")
            refresh_profile()

        async def toggle_sync() -> None:
            """Persist the sync switch; sync right away when it is turned on."""
            st.sync_profile = bool(sync.value)
            st.save()
            refresh_profile()
            if st.sync_profile and get_api_key():
                await sync_now()

        sync.on_value_change(toggle_sync)
        refresh_profile()
        ui.separator()
        ui.label("intervals.icu").classes("section-title mt-2")
        ui.label(
            "Find your API key at intervals.icu → Settings → Developer Settings. "
            "It is stored in the macOS Keychain."
        ).classes("hint")
        key = ui.input(
            "API key",
            password=True,
            password_toggle_button=True,
            placeholder="(saved)" if get_api_key() else "",
        )
        athlete = ui.input("Athlete ID", value=st.intervals_athlete_id).tooltip(
            "Leave 0 for your own account"
        )
        auto = ui.switch("Upload automatically after each ride", value=st.auto_upload)
        ui.separator()
        ui.label("Power matching (pedals as power source)").classes("section-title mt-2")
        max_off = ui.number("Max correction (W)", value=st.power_match.max_offset_w, min=0, max=150)
        settle = ui.number(
            "Settle time after target change (s)", value=st.power_match.settle_s, min=0, max=30
        )

        async def save() -> None:
            """Validate the form values and persist the settings."""
            # ui.number yields None when a field is cleared: keep the current value then.
            pm = st.power_match
            if not st.sync_profile:  # synced values come from intervals.icu only
                st.ftp = int(ftp.value) if ftp.value is not None else st.ftp
                st.weight_kg = float(weight.value) if weight.value is not None else st.weight_kg
            st.intervals_athlete_id = athlete.value.strip() or "0"
            st.auto_upload = bool(auto.value)
            if max_off.value is not None:
                pm.max_offset_w = float(max_off.value)
            if settle.value is not None:
                pm.settle_s = float(settle.value)
            new_key = bool(key.value)
            if new_key:
                set_api_key(key.value.strip())
                key.value = ""
            st.save()
            ui.notify("Settings saved", type="positive")
            if new_key and st.sync_profile:
                await sync_now()

        async def test_key() -> None:
            """Check the intervals.icu API key by fetching the athlete profile."""
            k = key.value.strip() or get_api_key()
            if not k:
                ui.notify("Enter an API key first", type="warning")
                return
            from smart_trainer.sync.intervals import IntervalsClient

            c = IntervalsClient(k, athlete.value.strip() or "0")
            try:
                a = await c.athlete()
                ui.notify(f"Connected as {a.get('name', 'athlete')}", type="positive")
            except Exception as exc:
                ui.notify(str(exc), type="negative")
            finally:
                await c.aclose()

        with ui.row().classes("mt-2"):
            ui.button("Save", icon="save", on_click=save).props("color=positive")
            ui.button("Test connection", icon="wifi_tethering", on_click=test_key).props(
                "color=secondary"
            )
