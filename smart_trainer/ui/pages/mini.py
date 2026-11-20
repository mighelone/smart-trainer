"""Mini overlay: a compact always-on-top window with key metrics and basic controls."""

from __future__ import annotations

from nicegui import ui

from smart_trainer.config import Settings
from smart_trainer.core.ride import RideState
from smart_trainer.ui.context import LiveRide
from smart_trainer.ui.pages.live import ride_status, start_ride, update_main_tiles
from smart_trainer.ui.theme import apply_theme
from smart_trainer.ui.widgets import CuePlayer, Tile, fmt_time, step_title
from smart_trainer.ui.window import MiniWindow


def build_mini_page(live: LiveRide, settings: Settings, window: MiniWindow) -> None:
    """Compact always-on-top overlay: key metrics and basic ride controls.

    Args:
        live (LiveRide): The ride state shown and controlled.
        settings (Settings): User settings (FTP, zones, cues).
        window (MiniWindow): Full app / mini overlay switcher.
    """
    ride = live.ride
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
                    ui.button(icon="play_arrow", on_click=lambda: start_ride(ride))
                    .props("color=positive dense")
                    .mark("mini-start")
                    .tooltip("Start")
                )
                btn_pause = (
                    ui.button(icon="pause", on_click=lambda: ride.toggle_pause())
                    .props("color=primary dense")
                    .mark("mini-pause")
                )
                with ui.row().classes("gap-1 no-wrap") as intensity_box:
                    ui.button(icon="remove", on_click=lambda: ride.change_intensity(-0.05)).props(
                        "color=secondary dense"
                    ).tooltip("Intensity -5%")
                    ui.button(icon="add", on_click=lambda: ride.change_intensity(0.05)).props(
                        "color=secondary dense"
                    ).mark("mini-plus").tooltip("Intensity +5%")
                ui.button(icon="open_in_full", on_click=window.exit).props(
                    "color=secondary dense"
                ).mark("mini-expand").tooltip("Back to the full app")
        with ui.row().classes("w-full items-center no-wrap gap-3"):
            step_label = ui.label("").classes("mini-step")
            step_bar = (
                ui.linear_progress(value=0, show_value=False)
                .props("rounded size=5px color=primary instant-feedback")
                .classes("step-progress grow")
            )

    cues = CuePlayer(ride, lambda: settings.interval_cues)

    def refresh() -> None:
        """Update tiles, step line and buttons from the engine state."""
        cues.check()
        riding = ride.state == RideState.RIDING
        update_main_tiles(live, settings, t_power, t_target, t_hr, t_cad)
        r = ride.runner
        if r:
            i, st, into, left = r.current()
            t_step.set(fmt_time(left), f"{i + 1}/{len(r.workout.steps)} · {r.intensity:.0%}")
            text = (
                f"{step_title(i, st)} · {fmt_time(r.elapsed)} of {fmt_time(r.workout.duration_s)}"
            )
            step_bar.set_value(min(1.0, into / st.duration_s) if st.duration_s else 0)
        else:
            t_step.set("--", "free ride" if riding else "")
            sel = None if riding else ride.next_workout
            text = (
                f"Free ride · {fmt_time(ride.seconds)}"
                if riding
                else (sel.name if sel else "Free ride: press start")
            )
            step_bar.set_value(0)
        status = ride_status(live)
        step_label.set_text(" · ".join(status) if status else text)
        step_label.classes(add="mini-warn" if status else "", remove="" if status else "mini-warn")
        btn_start.set_visibility(not riding)
        btn_pause.set_visibility(riding)
        btn_pause.props(f"icon={'play_arrow' if ride.manual_pause else 'pause'}")
        intensity_box.set_visibility(riding and r is not None)

    refresh()
    ui.timer(0.5, refresh)
