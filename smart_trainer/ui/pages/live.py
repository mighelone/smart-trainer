"""What the live screens (ride page and mini overlay) share: start, status, main tiles."""

from __future__ import annotations

from nicegui import ui

from smart_trainer.config import Settings
from smart_trainer.core.control import SPIRAL_RESUME_RPM, Mode
from smart_trainer.core.ride import Ride, RideState
from smart_trainer.core.state import Role, Snapshot
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


class MainTiles:
    """Power, target, heart-rate and cadence tiles of the live screens, with their zones."""

    def __init__(self, live: LiveRide, settings: Settings, compact: bool = False) -> None:
        """Create the four tiles in the current UI container.

        Args:
            live (LiveRide): The ride state the tiles show.
            settings (Settings): User settings (FTP and zones).
            compact (bool): Small tiles with short labels, for the mini overlay.
        """
        self.live = live
        self.settings = settings
        size = "sm" if compact else "lg"
        power, hr = ("Power", "HR") if compact else ("Power · 3s", "Heart rate")
        self.power = Tile(power, "W", icon="bolt", size=size, accent="#facc15")
        self.target = Tile("Target", "W", icon="adjust", size=size, accent="#4f8cff")
        self.hr = Tile(hr, "bpm", icon="favorite", size=size, accent="#f43f5e")
        self.cadence = Tile("Cadence", "rpm", icon="autorenew", size=size, accent="#a78bfa")

    def update(self) -> None:
        """Update the four tiles from the latest snapshot and the ride."""
        snap = self.live.snapshot()
        self._show_power(snap)
        self._show_target()
        self._show_hr(snap)
        self._show_cadence(snap)

    def _show_power(self, snap: Snapshot) -> None:
        """Show the 3 s power, where it comes from, and its zone.

        Args:
            snap (Snapshot): Latest metrics.
        """
        assignment = self.live.devices.assignment
        from_pedals = assignment.has_pedals and snap.power_source == assignment.power_id
        sub = f"from {'pedals' if from_pedals else 'trainer'}"
        if snap.pedal_power is not None and snap.trainer_power is not None:
            sub += f" · trainer {fmt(snap.trainer_power)} W"
        self.power.set(fmt(snap.power_avg), sub)
        self.power.set_zone(
            *power_zone(snap.power_avg, self.settings.ftp, self.settings.power_zones)
        )

    def _show_target(self) -> None:
        """Show the target power and what the trainer was last told."""
        target = self.live.ride.current_target()
        controller = self.live.controller
        cmd = controller.last_command
        self.target.set(
            fmt(target) if target is not None else "--",
            f"trainer command {cmd[1]:.0f} W"
            if cmd and cmd[0] == Mode.ERG
            else f"{controller.mode.value} mode",
        )
        self.target.set_zone(*power_zone(target, self.settings.ftp, self.settings.power_zones))

    def _show_hr(self, snap: Snapshot) -> None:
        """Show the heart rate, its share of the maximum, and its zone.

        Args:
            snap (Snapshot): Latest metrics.
        """
        max_hr = self.settings.max_hr
        self.hr.set(fmt(snap.hr), f"{snap.hr / max_hr:.0%} of max" if snap.hr and max_hr else "")
        self.hr.set_zone(*hr_zone(snap.hr, self.settings.hr_zones))

    def _show_cadence(self, snap: Snapshot) -> None:
        """Show the cadence with its target and the off-target alert.

        Args:
            snap (Snapshot): Latest metrics.
        """
        ride = self.live.ride
        sub, accent, badge = cadence_display(ride.cadence_target, ride.cadence_alert)
        self.cadence.set(fmt(snap.cadence), sub)
        self.cadence.set_zone(accent, badge)
