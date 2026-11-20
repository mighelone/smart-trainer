"""What the live screens (ride page and mini overlay) share: start, status, metric tiles."""

from __future__ import annotations

from nicegui import ui

from smart_trainer.config import Settings
from smart_trainer.core.control import SPIRAL_RESUME_RPM, Mode
from smart_trainer.core.ride import Ride, RideState
from smart_trainer.core.state import Role
from smart_trainer.ui.context import LiveRide
from smart_trainer.ui.widgets.text import fmt
from smart_trainer.ui.widgets.tiles import Tile, cadence_display, hr_zone, power_zone


def start_ride(ride: Ride) -> None:
    """Start a ride with the selected workout (or a free ride).

    Args:
        ride (Ride): The ride.
    """
    workout = ride.next_workout
    try:
        ride.start(workout)
    except RuntimeError as exc:
        ui.notify(str(exc), type="warning")
        return
    ui.notify(f"Started: {workout.name if workout else 'free ride'}")


def ride_status(live: LiveRide) -> list[str]:
    """Status messages for the ride (pause, auto-pause, ERG release, device warnings).

    Args:
        live (LiveRide): The ride state the live screens read.

    Returns:
        list[str]: Messages to show, empty when all is well.
    """
    ride, snapshot, devices = live.ride, live.snapshot(), live.devices
    status = []
    if ride.manual_pause:
        status.append("PAUSED")
    elif ride.state == RideState.RIDING and ride.auto_paused:
        status.append("Auto-paused: start pedalling")
    elif ride.state == RideState.RIDING and live.controller.erg_released:
        status.append(f"ERG released: spin up to {SPIRAL_RESUME_RPM} rpm")
    elif ride.cadence_alert and ride.cadence_target is not None:
        cad = fmt(snapshot.cadence)
        status.append(f"Cadence {ride.cadence_alert}: {cad} / {ride.cadence_target} rpm")
    status += snapshot.warnings
    if devices.device_for(Role.TRAINER) and not devices.trainer_connected():
        status.append("Trainer disconnected: reconnecting…")
    return status


def update_main_tiles(
    live: LiveRide, settings: Settings, t_power: Tile, t_target: Tile, t_hr: Tile, t_cad: Tile
) -> None:
    """Update the power, target, heart-rate and cadence tiles from the engine state.

    Args:
        live (LiveRide): The ride state the live screens read.
        settings (Settings): User settings (FTP and zones).
        t_power (Tile): 3 s power tile.
        t_target (Tile): Target power tile.
        t_hr (Tile): Heart-rate tile.
        t_cad (Tile): Cadence tile.
    """
    snap = live.snapshot()
    ftp = settings.ftp
    assignment = live.devices.assignment
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
    zones = settings.power_zones
    t_power.set(fmt(p3), sub)
    t_power.set_zone(*power_zone(p3, ftp, zones))
    target = live.ride.current_target()
    cmd = live.controller.last_command
    t_target.set(
        fmt(target) if target is not None else "--",
        f"trainer command {cmd[1]:.0f} W"
        if cmd and cmd[0] == Mode.ERG
        else f"{live.controller.mode.value} mode",
    )
    t_target.set_zone(*power_zone(target, ftp, zones))
    max_hr = settings.max_hr
    t_hr.set(fmt(snap.hr), f"{snap.hr / max_hr:.0%} of max" if snap.hr and max_hr else "")
    t_hr.set_zone(*hr_zone(snap.hr, settings.hr_zones))
    cad_sub, cad_accent, cad_badge = cadence_display(
        live.ride.cadence_target, live.ride.cadence_alert
    )
    t_cad.set(fmt(snap.cadence), cad_sub)
    t_cad.set_zone(cad_accent, cad_badge)
