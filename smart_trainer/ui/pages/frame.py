"""Common header of the pages: navigation and device status chips."""

from __future__ import annotations

from nicegui import ui

from smart_trainer.ble.device import Device
from smart_trainer.core.devices import DeviceHub
from smart_trainer.core.state import ConnState, Role
from smart_trainer.ui.context import AppContext
from smart_trainer.ui.theme import apply_theme

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
    Role.SHIFTER: "swap_vert",
}


def frame(ctx: AppContext, title: str) -> None:
    """Common header with navigation and device status chips.

    Args:
        ctx (AppContext): The app's objects.
        title (str): Page title shown in the header.
    """
    apply_theme()
    with ui.header().classes("app-header items-center justify-between px-6 py-2"):
        with ui.row().classes("items-center gap-3"):
            ui.icon("directions_bike", size="sm").classes("text-primary")
            ui.label("Smart Trainer").classes("brand")
            if ctx.fake:
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
            ui.button(icon="picture_in_picture_alt", on_click=ctx.window.enter).props(
                "flat dense padding='6px 10px' color=grey-5"
            ).classes("nav-btn").mark("mini").tooltip("Mini overlay: stays on top of other windows")
        chips = DeviceChips(ctx.engine.devices)
    ui.timer(2.0, chips.refresh)


class DeviceChips:
    """Header chips with the state of the trainer, power meter and HR strap.

    The chips are built once; :meth:`refresh` only changes the dot colour and the
    tooltip of a chip when they change (NiceGUI then sends just that).

    Attributes:
        ROLES (tuple[Role, ...]): Roles shown, in order.
        devices (DeviceHub): Devices and their roles.
    """

    ROLES = (Role.TRAINER, Role.POWER, Role.HR)

    def __init__(self, devices: DeviceHub) -> None:
        """Build the chips and show the current state.

        Args:
            devices (DeviceHub): Devices and their roles.
        """
        self.devices = devices
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
            color, tip = self.describe(role, self.devices.device_for(role))
            self._dots[role].style(replace=f"background: {color}")
            self._tips[role].set_text(tip)
