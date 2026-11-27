"""Devices page: paired devices, BLE scanning/pairing and guided calibration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from nicegui import ui

from smart_trainer.ble.device import (
    DRIVERS,
    Calibratable,
    CalibrationKind,
    CalibrationPhase,
    CalibrationProgress,
    Device,
)
from smart_trainer.config import Settings
from smart_trainer.core.calibration import CalibrationService
from smart_trainer.core.devices import DeviceHub
from smart_trainer.core.state import Role
from smart_trainer.ui.pages.frame import DOT_COLOR, ROLE_ICON

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from smart_trainer.ble.scanner import Discovered


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
    Role.SHIFTER: "Use as shifter",
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
        devices (DeviceHub): Paired devices.
        calibration (CalibrationService): Runs the calibration.
        device_id (str): Id of the device to calibrate.
        task (asyncio.Task | None): The running (or last) calibration.
    """

    def __init__(
        self,
        devices: DeviceHub,
        calibration: CalibrationService,
        dialog: ui.dialog,
        device_id: str,
        on_change: Callable[[], None],
    ) -> None:
        """Prepare the dialog; :meth:`open` renders and shows it.

        Args:
            devices (DeviceHub): Paired devices.
            calibration (CalibrationService): Runs the calibration.
            dialog (ui.dialog): Dialog to render into (cleared on open).
            device_id (str): Id of the device to calibrate.
            on_change (Callable[[], None]): Called when a calibration starts, to redraw
                the device list.
        """
        self.devices = devices
        self.calibration = calibration
        self.dialog = dialog
        self.device_id = device_id
        self.task: asyncio.Task | None = None
        self._on_change = on_change

    def open(self) -> None:
        """Render the guided calibration and open the dialog (calibratable devices only)."""
        dev = self.devices.get(self.device_id)
        if not isinstance(dev, Calibratable):
            return
        dialog = self.dialog
        dialog.clear()
        with dialog, ui.card().classes("w-[32rem] max-w-full"):
            spin_down = dev.CALIBRATION == CalibrationKind.SPIN_DOWN
            title = "Spin-down calibration" if spin_down else "Zero-offset calibration"
            ui.label(f"{title}: {dev.name}").classes("text-xl font-bold")
            with ui.column().classes("gap-1"):
                for number, text in enumerate(CALIBRATION_STEPS[dev.CALIBRATION], 1):
                    ui.label(f"{number}. {text}")
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
        reason = self.calibration.blocker(self.device_id)
        if reason:
            ui.notify(reason, type="warning")
            return
        self.task = asyncio.create_task(self.calibration.run(self.device_id))
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
        progress = self.calibration.progress
        if task is None or progress is None:
            return
        self.live.set_visibility(running)
        if running:
            self._show_progress(progress)
        elif progress.phase == CalibrationPhase.DONE:
            self.result_lbl.set_text(f"✓ {progress.message}")
            self.result_lbl.classes(replace="text-base text-positive font-semibold")
        else:
            self.result_lbl.set_text(f"✗ {progress.message}")
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

    def _show_progress(self, progress: CalibrationProgress) -> None:
        """Show the phase, instruction, speed and progress of a running calibration.

        Args:
            progress (CalibrationProgress): Latest progress.
        """
        self.phase_lbl.set_text(PHASE_LABEL.get(progress.phase, progress.phase.value))
        self.msg_lbl.set_text(progress.message)
        if progress.speed_kmh is not None:
            target = (
                f" / target {progress.target_low_kmh:.0f}-{progress.target_high_kmh:.0f} km/h"
                if progress.target_low_kmh and progress.target_high_kmh
                else ""
            )
            self.speed_lbl.set_text(f"{progress.speed_kmh:.1f} km/h{target}")
        else:
            self.speed_lbl.set_text("")
        if progress.fraction is None:
            self.bar.props("indeterminate")
        else:
            self.bar.props(remove="indeterminate")
            self.bar.set_value(progress.fraction)
        self.result_lbl.set_text("")


class DevicesPage:
    """Devices page widgets: paired devices and BLE scanning/pairing.

    Attributes:
        devices (DeviceHub): Paired devices and their roles.
        calibration (CalibrationService): Device calibration.
        settings (Settings): User settings (current role assignment).
    """

    def __init__(
        self, devices: DeviceHub, calibration: CalibrationService, settings: Settings
    ) -> None:
        """Build the page and refresh the paired devices every 2 s.

        Args:
            devices (DeviceHub): Paired devices and their roles.
            calibration (CalibrationService): Device calibration.
            settings (Settings): User settings (current role assignment).
        """
        self.devices = devices
        self.calibration = calibration
        self.settings = settings
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
        self.paired.clear()
        roles_by_dev: dict[str, list[str]] = {}
        for role, did in self.devices.assignment.roles.items():
            roles_by_dev.setdefault(did, []).append(role.value)
        with self.paired:
            if not self.devices:
                ui.label("No devices yet: scan below.").classes("hint")
            for did, dev in self.devices.items():
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
                blocker = self.calibration.blocker(did)
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
        await self.devices.remove(device_id)
        self.render_paired()

    def open_calibration(self, device_id: str) -> None:
        """Open the guided calibration dialog for a device.

        Args:
            device_id (str): Id of the device to calibrate.
        """
        CalibrationDialog(
            self.devices, self.calibration, self.cal_dialog, device_id, self.render_paired
        ).open()

    # ------------------------------------------------------------- scan
    async def do_scan(self) -> None:
        """Scan Bluetooth and the network together, and list what is found with role buttons.

        A failing scan is reported; the other one's devices are still listed.
        """
        from smart_trainer.ble.scanner import scan
        from smart_trainer.obc.discovery import browse

        self.found.clear()
        self.scan_btn.props("loading")
        try:
            ble, network = await asyncio.gather(scan(6.0), browse(6.0), return_exceptions=True)
        finally:
            self.scan_btn.props(remove="loading")
        results: list[Discovered] = []
        if isinstance(ble, BaseException):
            log.warning("Bluetooth scan failed: %s", ble)
            ui.notify(
                f"Bluetooth scan failed: {ble}. Check that Bluetooth is on and allowed "
                "for your terminal in System Settings → Privacy & Security → Bluetooth.",
                type="negative",
                multi_line=True,
            )
        else:
            results += ble
        if isinstance(network, BaseException):
            log.warning("network scan for BikeControl failed: %s", network)
        else:
            results += network
        with self.found:
            if not results:
                ui.label("Nothing found.").classes("hint")
            for found in results:
                self._found_card(found)

    def _found_card(self, found: Discovered) -> None:
        """Render one discovered device with a button per role it can take.

        Args:
            found (Discovered): The discovered device.
        """
        with ui.card().classes("w-full panel"), ui.row().classes("items-center w-full"):
            with ui.column().classes("gap-0"):
                ui.label(found.name).classes("list-title")
                signal = "network" if found.rssi is None else f"RSSI {found.rssi}"
                details = [", ".join(sorted(found.roles)), signal]
                ui.label(" · ".join(details)).classes("list-sub")
            ui.space()
            driver = DRIVERS[found.drivers[0]]
            for role in USE_LABEL:
                if role in driver.ROLES:
                    ui.button(
                        USE_LABEL[role],
                        icon=ROLE_ICON[role],
                        on_click=lambda role=role: self.use(found, role),
                    ).props("color=secondary")

    def use(self, found: Discovered, role: Role) -> None:
        """Assign a discovered device to a role, pairing it if needed.

        Args:
            found (Discovered): The discovered device.
            role (Role): The role to assign.
        """
        from smart_trainer.ble.scanner import make_device

        dev = self.devices.get(found.address) or make_device(
            found.drivers[0], found.address, found.name
        )
        roles = roles_for(role, self.settings)
        if dev.id in self.devices:
            self.devices.assign(dev.id, roles)
        else:
            self.devices.pair(dev, roles)
        ui.notify(f"{found.name} assigned to {role.value}")
        self.render_paired()
