"""FTMS smart trainer driver (Zwift Hub, Wahoo KICKR, Tacx Neo, Elite, Saris, ...)."""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient

from smart_trainer.ble import uuids
from smart_trainer.ble.bleak_device import BleakDevice
from smart_trainer.ble.device import (
    Calibratable,
    CalibrationError,
    CalibrationKind,
    CalibrationPhase,
    CalibrationProgress,
    CalibrationResult,
    ControllableTrainer,
    DriverId,
    ProgressCallback,
    register,
)
from smart_trainer.ble.parsers import (
    FtmsOp,
    FtmsStatus,
    MachineStatus,
    ResultCode,
    SpinDownStatus,
    cmd_request_control,
    cmd_set_simulation,
    cmd_set_target_power,
    cmd_set_target_resistance,
    cmd_spin_down,
    cmd_start,
    ftms_spin_down_supported,
    parse_control_response,
    parse_indoor_bike_data,
    parse_machine_status,
    parse_spin_down_targets,
    result_text,
)
from smart_trainer.core.state import Reading, Role

log = logging.getLogger(__name__)


@register
class FtmsTrainer(BleakDevice, ControllableTrainer, Calibratable):
    """Smart trainer implementing the Fitness Machine Service (FTMS).

    Attributes:
        RESPONSE_TIMEOUT (float): Seconds to wait for a control point response.
        SPIN_DOWN_TIMEOUT (float): Seconds a whole spin-down may take.
        has_control (bool): Whether control of the trainer has been granted.
        speed_kmh (float | None): Latest speed reported by the trainer.
    """

    DRIVER = DriverId.FTMS
    SERVICE_UUID = uuids.FTMS_SERVICE
    ROLES = frozenset({Role.TRAINER, Role.POWER, Role.CADENCE, Role.SPEED})
    PRIORITY = 10
    RESPONSE_TIMEOUT = 3.0
    SPIN_DOWN_TIMEOUT = 90.0
    CALIBRATION = CalibrationKind.SPIN_DOWN

    def __init__(self, address: str, name: str = "") -> None:
        """Initialize the trainer.

        Args:
            address (str): BLE address (or platform UUID on macOS) of the trainer.
            name (str): Human-readable device name; defaults to the address.
        """
        super().__init__(address, name)
        self._responses: asyncio.Queue = asyncio.Queue()
        self._status: asyncio.Queue[MachineStatus] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self.has_control = False
        self.speed_kmh: float | None = None

    async def on_connected(self, client: BleakClient) -> None:
        """Subscribe to bike data and control point notifications, then take control.

        Args:
            client (BleakClient): The connected bleak client.
        """
        self.has_control = False
        self._responses = asyncio.Queue()
        self._status = asyncio.Queue()
        self.speed_kmh = None

        def on_bike_data(_char, data: bytearray) -> None:
            """Parse an Indoor Bike Data notification and emit a reading.

            Args:
                _char (BleakGATTCharacteristic): Characteristic that sent the notification.
                data (bytearray): Raw notification payload.
            """
            d = parse_indoor_bike_data(bytes(data))
            if d.speed_kmh is not None:
                self.speed_kmh = d.speed_kmh
            self.emit(
                Reading(
                    self.id,
                    power=d.power,
                    cadence=d.cadence_rpm,
                    speed_kmh=d.speed_kmh,
                    hr=d.heart_rate or None,
                )
            )

        def on_cp(_char, data: bytearray) -> None:
            """Queue a parsed control point response.

            Args:
                _char (BleakGATTCharacteristic): Characteristic that sent the notification.
                data (bytearray): Raw notification payload.
            """
            resp = parse_control_response(bytes(data))
            if resp:
                self._responses.put_nowait(resp)

        def on_status(_char, data: bytearray) -> None:
            """Queue a parsed machine status notification.

            Args:
                _char (BleakGATTCharacteristic): Characteristic that sent the notification.
                data (bytearray): Raw notification payload.
            """
            st = parse_machine_status(bytes(data))
            if st is None:
                return
            if st.op == FtmsStatus.CONTROL_LOST:
                self.has_control = False
            self._status.put_nowait(st)

        await client.start_notify(uuids.FTMS_CONTROL_POINT, on_cp)
        await client.start_notify(uuids.INDOOR_BIKE_DATA, on_bike_data)
        try:  # optional: only needed for spin-down calibration
            await client.start_notify(uuids.FTMS_STATUS, on_status)
        except Exception as exc:
            log.info("%s: no machine status notifications (%s)", self.name, exc)
        await self._take_control()

    async def _take_control(self) -> None:
        """Request control of the trainer and start it if granted."""
        r = await self._write(cmd_request_control())
        if r is not None and r.ok:
            await self._write(cmd_start())
            self.has_control = True
        else:
            log.warning("%s: request control refused (%s)", self.name, r)

    async def _write(self, payload: bytes):
        """Write a control point command and wait for its response.

        Args:
            payload (bytes): Encoded control point command.

        Returns:
            ControlResponse | None: The matching response, or ``None`` if not
            connected or no response arrived within ``RESPONSE_TIMEOUT``.
        """
        client = self.client
        if client is None:
            return None
        async with self._lock:
            while not self._responses.empty():
                self._responses.get_nowait()
            await client.write_gatt_char(uuids.FTMS_CONTROL_POINT, payload, response=True)
            try:
                while True:
                    resp = await asyncio.wait_for(self._responses.get(), self.RESPONSE_TIMEOUT)
                    if resp.request_op == payload[0]:
                        return resp
            except TimeoutError:
                log.warning("%s: no response to op 0x%02x", self.name, payload[0])
                return None

    async def _command(self, payload: bytes) -> bool:
        """Send a control command, (re)taking control of the trainer if needed.

        Args:
            payload (bytes): Encoded control point command.

        Returns:
            bool: ``True`` if the trainer acknowledged the command successfully.
        """
        if self.client is None:
            return False
        if not self.has_control:
            await self._take_control()
        r = await self._write(payload)
        if r is not None and r.result == ResultCode.CONTROL_NOT_PERMITTED:  # retake once
            await self._take_control()
            r = await self._write(payload)
        return bool(r and r.ok)

    async def set_erg(self, watts: float) -> None:
        """Set ERG mode with a target power.

        Args:
            watts (float): Target power in watts.
        """
        await self._command(cmd_set_target_power(int(watts)))

    async def set_resistance(self, pct: float) -> None:
        """Set a fixed resistance level.

        Args:
            pct (float): Resistance level as a percentage (0-100).
        """
        await self._command(cmd_set_target_resistance(pct))

    async def set_slope(self, grade_pct: float) -> None:
        """Set simulation mode with a road grade.

        Args:
            grade_pct (float): Road grade in percent.
        """
        await self._command(cmd_set_simulation(grade_pct))

    async def _spin_down_supported(self) -> bool | None:
        """Read the FTMS Feature characteristic to check spin-down support.

        Returns:
            bool | None: The advertised support, or ``None`` if it can't be read.
        """
        client = self.client
        if client is None:
            return None
        try:
            return ftms_spin_down_supported(bytes(await client.read_gatt_char(uuids.FTMS_FEATURE)))
        except Exception as exc:
            log.info("%s: cannot read FTMS features (%s)", self.name, exc)
            return None

    async def calibrate(self, on_progress: ProgressCallback) -> CalibrationResult:
        """Run an FTMS spin-down calibration.

        The rider pedals up to the target speed the trainer returns, stops pedalling
        when the trainer says so, and the trainer measures the coast-down.

        Args:
            on_progress (ProgressCallback): Called with every progress update.

        Returns:
            CalibrationResult: The outcome.

        Raises:
            CalibrationError: If the trainer refuses, reports an error, times out or
                disconnects.
        """
        if self.client is None:
            raise CalibrationError("The trainer is not connected.")
        on_progress(
            CalibrationProgress(CalibrationPhase.STARTING, "Asking the trainer for a spin-down…")
        )
        supported = await self._spin_down_supported()
        hint = (
            " This trainer does not advertise FTMS spin-down: it may calibrate itself "
            "automatically or need its own app."
            if supported is False
            else ""
        )
        if not self.has_control:
            await self._take_control()
        while not self._status.empty():
            self._status.get_nowait()
        r = await self._write(cmd_spin_down(start=True))
        if r is not None and r.result == ResultCode.CONTROL_NOT_PERMITTED:  # retake once
            await self._take_control()
            r = await self._write(cmd_spin_down(start=True))
        if r is None:
            raise CalibrationError("The trainer did not answer the spin-down request." + hint)
        if not r.ok:
            why = result_text(r.result)
            raise CalibrationError(f"The trainer refused the spin-down ({why})." + hint)
        low, high = parse_spin_down_targets(r.params) or (None, None)
        try:
            return await self._run_spin_down(on_progress, low, high)
        finally:
            self.has_control = False  # retake control (and restart) on the next command

    async def _run_spin_down(
        self, on_progress: ProgressCallback, low: float | None, high: float | None
    ) -> CalibrationResult:
        """Follow the trainer's spin-down status until it succeeds or fails.

        Args:
            on_progress (ProgressCallback): Called with every progress update.
            low (float | None): Lower target speed in km/h, if the trainer sent one.
            high (float | None): Upper target speed in km/h, if the trainer sent one.

        Returns:
            CalibrationResult: The outcome.

        Raises:
            CalibrationError: On a reported error, a timeout or a disconnection.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.SPIN_DOWN_TIMEOUT
        phase = CalibrationPhase.SPEED_UP
        coast_from: float | None = None
        target = f"{high:.0f} km/h" if high else "the target speed"
        while True:
            if self.client is None:
                raise CalibrationError("The trainer disconnected during the spin-down.")
            if loop.time() > deadline:
                raise CalibrationError("The spin-down timed out. Try again.")
            speed = self.speed_kmh
            if phase == CalibrationPhase.SPEED_UP:
                msg = f"Pedal up to {target} and hold it until the trainer says stop."
                frac = min(1.0, (speed or 0) / high) if high else None
            else:
                msg = "Stop pedalling and let the flywheel coast. Don't touch the pedals."
                frac = 1 - min(1.0, (speed or 0) / coast_from) if coast_from else None
            on_progress(CalibrationProgress(phase, msg, speed, low, high, frac))
            try:
                st = await asyncio.wait_for(self._status.get(), 0.5)
            except TimeoutError:
                continue
            if st.op == FtmsStatus.CONTROL_LOST:
                raise CalibrationError("Another app took control of the trainer.")
            if st.op != FtmsStatus.SPIN_DOWN_STATUS or not st.params:
                continue
            code = st.params[0]
            if code == SpinDownStatus.STOP_PEDALING:
                phase, coast_from = CalibrationPhase.COAST, max(speed or 0, high or 0) or None
            elif code == SpinDownStatus.SUCCESS:
                return CalibrationResult("Spin-down calibration complete.")
            elif code == SpinDownStatus.ERROR:
                raise CalibrationError(
                    "The trainer reported a spin-down error. Warm up for 10 minutes, then "
                    "try again, and don't pedal while it coasts."
                )


__all__ = ["FtmsTrainer", "FtmsOp"]
