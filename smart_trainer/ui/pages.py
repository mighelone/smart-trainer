"""NiceGUI pages. The UI only reads Engine state and calls Engine methods."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from nicegui import ui

from smart_trainer.ble.device import DRIVERS
from smart_trainer.config import get_api_key, set_api_key
from smart_trainer.core.control import Mode
from smart_trainer.core.engine import Engine, HistoryPoint, RideState
from smart_trainer.core.library import builtin_workouts
from smart_trainer.core.recorder import Recorder, list_sessions
from smart_trainer.core.state import ConnState, Role
from smart_trainer.core.workout import Workout, parse_zwo
from smart_trainer.ui.widgets import Tile, fmt, fmt_time, workout_chart

log = logging.getLogger(__name__)


class AppState:
    engine: Engine
    selected: Workout | None = None
    fake: bool = False


S = AppState()

STATE_COLOR = {
    ConnState.CONNECTED: "positive",
    ConnState.CONNECTING: "warning",
    ConnState.DISCONNECTED: "negative",
}
ROLE_ICON = {
    Role.TRAINER: "pedal_bike",
    Role.POWER: "bolt",
    Role.HR: "favorite",
    Role.CADENCE: "autorenew",
    Role.SPEED: "speed",
}


def frame(title: str) -> None:
    """Common header with navigation and device status chips."""
    with ui.header().classes("items-center justify-between px-4 py-2 bg-slate-900"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("directions_bike", size="md")
            ui.label("Smart Trainer").classes("text-lg font-bold")
            ui.label(f"· {title}").classes("opacity-60")
            if S.fake:
                ui.badge("DEMO: simulated devices", color="orange")
        with ui.row().classes("gap-1"):
            for label, path in (
                ("Ride", "/"),
                ("Workouts", "/workouts"),
                ("Devices", "/devices"),
                ("History", "/history"),
                ("Settings", "/settings"),
            ):
                ui.button(label, on_click=lambda p=path: ui.navigate.to(p)).props(
                    "flat color=white dense"
                )
        chips = ui.row().classes("gap-1")

    def refresh_chips() -> None:
        chips.clear()
        e = S.engine
        with chips:
            for role in (Role.TRAINER, Role.POWER, Role.HR):
                dev = e.device_for(role)
                color = STATE_COLOR[dev.state] if dev else "grey"
                tip = (
                    f"{role.value}: {dev.name} ({dev.state})"
                    if dev
                    else f"{role.value}: not paired"
                )
                ui.chip(icon=ROLE_ICON[role], color=color).props("dense square").tooltip(tip)

    refresh_chips()
    ui.timer(2.0, refresh_chips)


# ===================================================================== RIDE
@ui.page("/")
def ride_page() -> None:
    e = S.engine
    frame("Ride")
    ftp = e.settings.ftp

    with ui.column().classes("w-full p-4 gap-3"):
        warn = ui.label("").classes("text-orange-400 font-semibold")
        with ui.row().classes("w-full gap-3 no-wrap"):
            t_power = Tile("Power (3s)", "W", big=True, color="text-yellow-300")
            t_target = Tile("Target", "W", big=True, color="text-sky-300")
            t_hr = Tile("Heart rate", "bpm", color="text-rose-400")
            t_cad = Tile("Cadence", "rpm")
        with ui.row().classes("w-full gap-3 no-wrap"):
            t_time = Tile("Elapsed", "")
            t_step = Tile("Interval left", "")
            t_speed = Tile("Speed", "km/h")
            t_bal = Tile("Balance L/R", "")
        step_label = ui.label("").classes("text-xl")
        next_label = ui.label("").classes("opacity-70")

        holder = ui.column().classes("w-full")
        chart_ref: dict = {"chart": None, "workout": object()}

        def ensure_chart() -> None:
            wo = (
                e.runner.workout
                if e.runner
                else (S.selected if e.state == RideState.IDLE else None)
            )
            if chart_ref["workout"] is wo:
                return
            holder.clear()
            with holder:
                chart_ref["chart"] = workout_chart(wo, ftp)
            chart_ref["workout"] = wo

        # ---------------------------------------------------------- controls
        with ui.row().classes("items-center gap-2 w-full"):
            btn_start = ui.button("Start", icon="play_arrow", on_click=lambda: start()).props(
                "color=positive size=lg"
            )
            btn_pause = ui.button("Pause", icon="pause", on_click=lambda: e.toggle_pause()).props(
                "size=lg"
            )
            btn_skip = ui.button("Skip", icon="skip_next", on_click=lambda: e.skip_step())
            btn_lap = ui.button("Lap", icon="flag", on_click=lambda: e.lap())
            with ui.row().classes("items-center gap-1") as intensity_box:
                ui.button(icon="remove", on_click=lambda: e.change_intensity(-0.05)).tooltip(
                    "Intensity -5%"
                )
                intensity = ui.label("100%").classes("text-lg tabular-nums w-14 text-center")
                ui.button(icon="add", on_click=lambda: e.change_intensity(0.05)).tooltip(
                    "Intensity +5%"
                )
            erg = ui.switch("ERG", value=e.erg_enabled, on_change=lambda ev: e.set_erg(ev.value))
            ui.space()
            btn_finish = ui.button("Finish", icon="stop", on_click=lambda: confirm_finish()).props(
                "color=negative size=lg"
            )

        with (
            ui.card().classes("w-full") as manual_card,
            ui.row().classes("items-center gap-4 w-full"),
        ):
            ui.label("Manual control").classes("font-semibold")
            mode = ui.toggle(
                {"resistance": "Resistance", "slope": "Slope", "erg": "Fixed ERG"},
                value=e.controller.mode.value if e.runner is None else "resistance",
                on_change=lambda ev: set_manual_mode(ev.value),
            )
            slider = ui.slider(
                min=0,
                max=100,
                step=1,
                value=e.controller.resistance_pct,
                on_change=lambda ev: set_manual_value(ev.value),
            ).classes("grow")
            slider_label = ui.label("").classes("w-24 tabular-nums")
        with ui.row().classes("items-center gap-4"):
            ui.label("Power source")
            ui.toggle(
                {"pedals": "Pedals", "trainer": "Trainer"},
                value=e.settings.power_source,
                on_change=lambda ev: e.set_power_source(ev.value),
            )

    def set_manual_mode(m: str) -> None:
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
        c = e.controller
        if c.mode == Mode.RESISTANCE:
            c.resistance_pct = v
        elif c.mode == Mode.SLOPE:
            c.slope_pct = v
        else:
            c.erg_target = v

    def start() -> None:
        e.start_ride(S.selected)
        ui.notify(f"Started: {S.selected.name if S.selected else 'free ride'}")

    async def do_finish(dialog, upload: bool) -> None:
        dialog.close()
        n = ui.notification("Saving session…", spinner=True, timeout=None)
        fit = await e.finish(upload=upload)
        n.dismiss()
        S.selected = None
        ui.notify(f"Saved {fit.name}" if fit else "Nothing recorded", type="positive")
        for m in list(e.messages)[-1:]:
            ui.notify(m)

    def confirm_finish() -> None:
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
        if e.state != RideState.IDLE:
            return
        crashed = e.unfinished_sessions()
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
                d.close()
                e.resume_session(info)
                ui.notify("Session resumed: start pedalling")

            async def finish():
                d.close()
                fit = await e.finish_crashed(info)
                ui.notify(f"Saved {fit.name if fit else 'session'}")

            def discard():
                d.close()
                e.discard(info)
                ui.notify("Session moved to sessions/discarded")

            with ui.row():
                ui.button("Resume", icon="play_arrow", on_click=resume).props("color=positive")
                ui.button("Finish & save", icon="save", on_click=finish)
                ui.button("Discard", icon="delete", on_click=discard).props("flat color=negative")
        d.open()

    recovery_dialog()

    # ------------------------------------------------------------- refresh
    def refresh() -> None:
        ensure_chart()
        snap = e.snapshot
        riding = e.state == RideState.RIDING
        pedal_id = e.selector.assignment.get(Role.POWER)
        p3 = e.selector.avg_power(snap.power_source) if snap.power_source else None
        src = (
            "pedals"
            if snap.power_source
            and snap.power_source == pedal_id
            and snap.power_source != e.selector.trainer_id
            else "trainer"
        )
        sub = f"{src}" + (
            f" · trainer {fmt(snap.trainer_power)}"
            if snap.pedal_power is not None and snap.trainer_power is not None
            else ""
        )
        t_power.set(fmt(p3), sub)
        target = e.current_target()
        cmd = e.controller.last_command
        t_target.set(
            fmt(target) if target is not None else "--",
            f"trainer cmd {cmd[1]:.0f} W"
            if cmd and cmd[0] == Mode.ERG
            else e.controller.mode.value,
        )
        t_hr.set(fmt(snap.hr))
        t_cad.set(fmt(snap.cadence))
        t_speed.set(fmt(snap.speed_kmh, 1))
        t_bal.set(
            f"{snap.balance_left:.0f}/{100 - snap.balance_left:.0f}" if snap.balance_left else "--"
        )
        r = e.runner
        if r:
            i, st, into, left = r.current()
            t_time.set(fmt_time(r.elapsed), f"of {fmt_time(r.workout.duration_s)}")
            t_step.set(fmt_time(left), f"step {i + 1}/{len(r.workout.steps)}")
            label = st.text or ("Free ride" if st.free_ride else "")
            cad = f" · {st.cadence} rpm" if st.cadence else ""
            step_label.set_text(f"{label}{cad}")
            nxt = r.next_step()
            nt = nxt.target_w(0, r.ftp) if nxt else None
            next_label.set_text(
                f"Next: {nxt.text or ''} {fmt(nt)} W for {fmt_time(nxt.duration_s)}"
                if nxt
                else "Last interval"
            )
            intensity.set_text(f"{r.intensity * 100:.0f}%")
        else:
            t_time.set(fmt_time(e.ride_seconds if riding else 0), "free ride" if riding else "")
            t_step.set("--")
            step_label.set_text(S.selected.name if (S.selected and not riding) else "")
            next_label.set_text("")
        status = []
        if e.manual_pause:
            status.append("PAUSED")
        elif riding and e.auto_paused:
            status.append("Auto-paused: start pedalling")
        status += snap.warnings
        if e.device_for(Role.TRAINER) and not e.trainer_connected():
            status.append("Trainer disconnected: reconnecting…")
        warn.set_text(" · ".join(status))
        btn_start.set_visibility(not riding)
        for b in (btn_pause, btn_finish, btn_lap):
            b.set_visibility(riding)
        btn_skip.set_visibility(riding and r is not None)
        btn_pause.set_text("Resume" if e.manual_pause else "Pause")
        erg.set_visibility(r is not None)
        intensity_box.set_visibility(r is not None)
        manual_card.set_visibility(not (r and e.erg_enabled))
        c = e.controller
        unit = {"resistance": "%", "slope": "% grade", "erg": " W"}[c.mode.value]
        val = {"resistance": c.resistance_pct, "slope": c.slope_pct, "erg": c.erg_target}[
            c.mode.value
        ]
        slider_label.set_text(f"{val:g}{unit}")
        if mode.value != c.mode.value and not (r and e.erg_enabled):
            mode.value = c.mode.value

    tick = {"n": 0}

    def refresh_chart() -> None:
        def x_of(p: HistoryPoint) -> float:
            return p.t + offset

        ch = chart_ref["chart"]
        if ch is None or not e.history:
            return
        hist = list(e.history)
        step = max(1, len(hist) // 1500)
        if e.runner is not None:
            # x = workout elapsed: approximate with recorded active seconds
            offset = e.runner.elapsed - hist[-1].t
            x_of = lambda p: p.t + offset  # noqa: E731
        ch.options["series"][1]["data"] = [
            [round(x_of(p)), p.power] for p in hist[::step] if p.power is not None
        ]
        ch.options["series"][2]["data"] = [[round(x_of(p)), p.hr] for p in hist[::step] if p.hr]
        if e.runner is None:
            end = max(600, hist[-1].t)
            ch.options["xAxis"]["max"] = end
            ch.options["xAxis"]["min"] = max(0, end - 1200)
            ch.options["series"][0]["data"] = []
        ch.update()

    def loop() -> None:
        refresh()
        tick["n"] += 1
        if tick["n"] % 2 == 0:
            refresh_chart()

    ui.timer(0.5, loop)


# ================================================================= WORKOUTS
@ui.page("/workouts")
def workouts_page() -> None:
    e = S.engine
    frame("Workouts")
    with ui.column().classes("w-full p-4 gap-4"):
        if S.selected:
            ui.label(f"Selected: {S.selected.name}").classes("text-lg text-positive")
        with ui.row().classes("items-center gap-2"):
            ui.label("intervals.icu").classes("text-xl font-bold")
            ui.button("Refresh", icon="refresh", on_click=lambda: load())
            ui.button("Free ride", icon="all_inclusive", on_click=lambda: choose(None))
            ui.upload(
                label="Import .zwo", auto_upload=True, on_upload=lambda ev: import_zwo(ev)
            ).props("accept=.zwo flat dense").classes("w-56")
        status = ui.label("")
        listing = ui.column().classes("w-full gap-3")
        ui.label("Built-in workouts").classes("text-xl font-bold mt-4")
        builtin = ui.column().classes("w-full gap-3")

    def choose(wo: Workout | None) -> None:
        S.selected = wo
        ui.notify(f"Selected {wo.name}" if wo else "Free ride selected")
        ui.navigate.to("/")

    async def import_zwo(ev) -> None:
        try:
            wo = parse_zwo(await ev.file.text())
        except Exception as exc:
            ui.notify(f"Invalid .zwo file: {exc}", type="negative")
            return
        choose(wo)

    def card(wo: Workout, subtitle: str, where=None) -> None:
        with where or listing, ui.card().classes("w-full"):
            with ui.row().classes("items-center w-full"):
                with ui.column().classes("gap-0"):
                    ui.label(wo.name).classes("text-lg font-semibold")
                    ui.label(
                        f"{subtitle} · {fmt_time(wo.duration_s)} · {len(wo.steps)} steps"
                    ).classes("opacity-70")
                ui.space()
                ui.button("Ride this", icon="play_arrow", on_click=lambda w=wo: choose(w)).props(
                    "color=positive"
                )
            workout_chart(wo, e.settings.ftp, height="h-28", live=False)

    async def load() -> None:
        listing.clear()
        key = get_api_key()
        if not key:
            status.set_text("Add your intervals.icu API key in Settings to load planned workouts.")
            return
        from smart_trainer.sync.intervals import IntervalsClient

        status.set_text("Loading…")
        client = IntervalsClient(key, e.settings.intervals_athlete_id)
        try:
            today = date.today()
            planned = await client.list_workouts(
                today - timedelta(days=2), today + timedelta(days=7), e.settings.ftp
            )
        except Exception as exc:
            status.set_text(f"Could not load workouts: {exc}")
            return
        finally:
            await client.aclose()
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
@ui.page("/devices")
def devices_page() -> None:
    e = S.engine
    frame("Devices")
    with ui.column().classes("w-full p-4 gap-4"):
        ui.label("Paired devices").classes("text-xl font-bold")
        paired = ui.column().classes("w-full gap-2")
        with ui.row().classes("items-center gap-2"):
            ui.label("Find devices").classes("text-xl font-bold")
            scan_btn = ui.button("Scan", icon="bluetooth_searching", on_click=lambda: do_scan())
        ui.label(
            "Wake devices up first: pedal to wake the trainer and the Assioma pedals, "
            "and put on the HRM strap."
        ).classes("opacity-70")
        found = ui.column().classes("w-full gap-2")

    def render_paired() -> None:
        paired.clear()
        roles_by_dev: dict[str, list[str]] = {}
        for role, did in e.selector.assignment.items():
            roles_by_dev.setdefault(did, []).append(role.value)
        with paired:
            if not e.devices:
                ui.label("No devices yet: scan below.").classes("opacity-70")
            for did, dev in e.devices.items():
                with ui.card().classes("w-full"), ui.row().classes("items-center w-full"):
                    ui.icon("circle", color=STATE_COLOR[dev.state]).classes("text-xs")
                    with ui.column().classes("gap-0"):
                        ui.label(dev.name).classes("font-semibold")
                        used_for = ", ".join(sorted(roles_by_dev.get(did, []))) or "-"
                        ui.label(f"{dev.DRIVER} · used for: {used_for}").classes(
                            "text-sm opacity-70"
                        )
                    ui.space()

                    async def remove(d=did):
                        await e.remove_device(d)
                        e.settings.save()
                        render_paired()

                    ui.button(icon="link_off", on_click=remove).props("flat").tooltip("Forget")

    async def do_scan() -> None:
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
                ui.label("Nothing found.").classes("opacity-70")
            for r in results:
                with ui.card().classes("w-full"), ui.row().classes("items-center w-full"):
                    with ui.column().classes("gap-0"):
                        ui.label(r.name).classes("font-semibold")
                        ui.label(f"{', '.join(sorted(r.roles))} · RSSI {r.rssi}").classes(
                            "text-sm opacity-70"
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
                            )

    def use(r, role: Role) -> None:
        from smart_trainer.ble.scanner import make_device

        dev = e.devices.get(r.address) or make_device(r.drivers[0], r.address, r.name)
        roles = [role]
        if role == Role.POWER:
            roles.append(Role.CADENCE)
        if role == Role.TRAINER and Role.POWER.value not in e.settings.role_assignment:
            roles += [Role.POWER, Role.CADENCE]
        if role == Role.TRAINER:
            roles.append(Role.SPEED)
        if dev.id in e.devices:
            for ro in roles:
                e.settings.role_assignment[ro.value] = dev.id
            e._apply_assignment()
            e.settings.save()
        else:
            e.pair(dev, roles)
        ui.notify(f"{r.name} assigned to {role.value}")
        render_paired()

    render_paired()
    ui.timer(2.0, render_paired)


# ================================================================== HISTORY
@ui.page("/history")
def history_page() -> None:
    e = S.engine
    frame("History")
    with ui.column().classes("w-full p-4 gap-2"):
        ui.label(f"Sessions are stored in {e.sessions}").classes("text-sm opacity-60")
        listing = ui.column().classes("w-full gap-2")

    def render() -> None:
        listing.clear()
        with listing:
            for s in list_sessions(e.sessions):
                with ui.card().classes("w-full"), ui.row().classes("items-center w-full"):
                    icon, color = {
                        "uploaded": ("cloud_done", "positive"),
                        "finished": ("save", "primary"),
                        "active": ("warning", "warning"),
                    }.get(s.status, ("help", "grey"))
                    ui.icon(icon, color=color, size="sm")
                    with ui.column().classes("gap-0"):
                        ui.label(s.name).classes("font-semibold")
                        extra = f" · upload error: {s.upload_error}" if s.upload_error else ""
                        when = f"{s.started:%a %d %b %Y %H:%M}"
                        ui.label(f"{when} · {fmt_time(s.elapsed_s)} · {s.status}{extra}").classes(
                            "text-sm opacity-70"
                        )
                    ui.space()
                    fit = s.path.with_suffix(".fit")
                    if s.status == "finished" and fit.exists():

                        async def up(s=s, fit=fit):
                            rec = Recorder.open(s.path)
                            try:
                                ok = await e.upload(rec, fit, s)
                            finally:
                                rec.close()
                            ui.notify(
                                "Uploaded" if ok else e.messages[-1],
                                type="positive" if ok else "negative",
                            )
                            render()

                        ui.button("Upload", icon="cloud_upload", on_click=up)

    render()


# ================================================================= SETTINGS
@ui.page("/settings")
def settings_page() -> None:
    e = S.engine
    st = e.settings
    frame("Settings")
    with ui.column().classes("p-4 gap-3 w-full max-w-xl [&_.q-field]:w-full"):
        ftp = ui.number("FTP (W)", value=st.ftp, min=50, max=600, step=1)
        weight = ui.number("Weight (kg)", value=st.weight_kg, min=30, max=200, step=0.5)
        ui.separator()
        ui.label("intervals.icu").classes("text-lg font-bold")
        ui.label(
            "Find your API key at intervals.icu → Settings → Developer Settings. "
            "It is stored in the macOS Keychain."
        ).classes("text-sm opacity-70")
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
        ui.label("Power matching (pedals as power source)").classes("text-lg font-bold")
        max_off = ui.number("Max correction (W)", value=st.power_match.max_offset_w, min=0, max=150)
        settle = ui.number(
            "Settle time after target change (s)", value=st.power_match.settle_s, min=0, max=30
        )

        async def save() -> None:
            # ui.number yields None when a field is cleared: keep the current value then.
            pm = st.power_match
            st.ftp = int(ftp.value) if ftp.value is not None else st.ftp
            st.weight_kg = float(weight.value) if weight.value is not None else st.weight_kg
            st.intervals_athlete_id = athlete.value.strip() or "0"
            st.auto_upload = bool(auto.value)
            if max_off.value is not None:
                pm.max_offset_w = float(max_off.value)
            if settle.value is not None:
                pm.settle_s = float(settle.value)
            if key.value:
                set_api_key(key.value.strip())
                key.value = ""
            st.save()
            ui.notify("Settings saved", type="positive")

        async def test_key() -> None:
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

        with ui.row():
            ui.button("Save", icon="save", on_click=save).props("color=positive")
            ui.button("Test connection", icon="wifi_tethering", on_click=test_key)
