"""Driver for BLE Cycling Power Service meters."""

from __future__ import annotations

import asyncio
import logging
import time

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
    DriverId,
    ProgressCallback,
    register,
)
from smart_trainer.ble.parsers import (
    CadenceCalculator,
    ControlResponse,
    CpOp,
    ResultCode,
    cmd_start_offset_compensation,
    format_offset,
    parse_cp_control_response,
    parse_cp_feature,
    parse_cycling_power,
    parse_offset,
    result_text,
)
from smart_trainer.core.state import Reading, Role

log = logging.getLogger(__name__)


@register
class PowerMeter(BleakDevice, Calibratable):
    """Any BLE Cycling Power meter (Favero Assioma, Garmin Rally, Stages, Quarq, ...).

    Provides power, cadence (derived from crank revolutions) and L/R balance, and
    zero-offset calibration through the Cycling Power Control Point.

    Attributes:
        OFFSET_TIMEOUT (float): Seconds to wait for the zero-offset response.
    """

    DRIVER = DriverId.CYCLING_POWER
    SERVICE_UUID = uuids.CYCLING_POWER_SERVICE
    ROLES = frozenset({Role.POWER, Role.CADENCE})
    PRIORITY = 5
    CALIBRATION = CalibrationKind.ZERO_OFFSET
    OFFSET_TIMEOUT = 15.0

    def __init__(self, address: str, name: str = "") -> None:
        """Initialize the power meter.

        Args:
            address (str): BLE address (or platform UUID on macOS) of the meter.
            name (str): Human-readable device name; defaults to the address.
        """
        super().__init__(address, name)
        self._responses: asyncio.Queue[ControlResponse] = asyncio.Queue()
        self._cp_client: BleakClient | None = None  # client the control point is subscribed on

    async def on_connected(self, client: BleakClient) -> None:
        """Subscribe to Cycling Power Measurement notifications.

        Args:
            client (BleakClient): The connected bleak client.
        """
        cadence = CadenceCalculator()
        last = [time.monotonic()]
        self._cp_client = None

        def handle(_char, data: bytearray) -> None:
            """Parse a Cycling Power Measurement notification and emit a reading.

            Args:
                _char (BleakGATTCharacteristic): Characteristic that sent the notification.
                data (bytearray): Raw notification payload.
            """
            m = parse_cycling_power(bytes(data))
            now = time.monotonic()
            dt, last[0] = now - last[0], now
            rpm = None
            if m.crank_revs is not None and m.crank_event_time is not None:
                rpm = cadence.update(m.crank_revs, m.crank_event_time, dt)
            self.emit(Reading(self.id, power=m.power, cadence=rpm, balance_left=m.balance_left_pct))

        await client.start_notify(uuids.CYCLING_POWER_MEASUREMENT, handle)

    async def _subscribe_control_point(self, client: BleakClient) -> None:
        """Enable control point indications once per connection.

        Args:
            client (BleakClient): The connected bleak client.

        Raises:
            CalibrationError: If the meter has no Cycling Power Control Point.
        """
        if self._cp_client is client:
            return

        def on_cp(_char, data: bytearray) -> None:
            """Queue a parsed control point response.

            Args:
                _char (BleakGATTCharacteristic): Characteristic that sent the indication.
                data (bytearray): Raw indication payload.
            """
            resp = parse_cp_control_response(bytes(data))
            if resp:
                self._responses.put_nowait(resp)

        try:
            await client.start_notify(uuids.CYCLING_POWER_CONTROL_POINT, on_cp)
        except Exception as exc:
            log.info("%s: no cycling power control point (%s)", self.name, exc)
            raise CalibrationError("This power meter does not support calibration.") from exc
        self._cp_client = client

    async def _read_torque_based(self, client: BleakClient) -> bool | None:
        """Read the Cycling Power Feature to learn whether the meter is torque based.

        Args:
            client (BleakClient): Connected client.

        Returns:
            bool | None: Whether offsets are torque values, or ``None`` if unreadable.
        """
        try:
            feature = parse_cp_feature(
                bytes(await client.read_gatt_char(uuids.CYCLING_POWER_FEATURE))
            )
        except Exception as exc:
            log.info("%s: cannot read cycling power features (%s)", self.name, exc)
            return None
        if not feature.offset_compensation:
            log.info("%s: offset compensation not advertised, trying anyway", self.name)
        return feature.torque_based

    async def _await_offset_response(self) -> ControlResponse:
        """Wait for the response to *Start Offset Compensation*.

        Returns:
            ControlResponse: The meter's response.

        Raises:
            CalibrationError: If the meter doesn't answer in time.
        """
        try:
            async with asyncio.timeout(self.OFFSET_TIMEOUT):
                while True:
                    resp = await self._responses.get()
                    if resp.request_op == CpOp.START_OFFSET_COMPENSATION:
                        return resp
        except TimeoutError:
            raise CalibrationError(
                "The power meter did not answer. Wake it up by turning the cranks, "
                "then keep them still and try again."
            ) from None

    async def calibrate(self, on_progress: ProgressCallback) -> CalibrationResult:
        """Run a zero-offset calibration (*Start Offset Compensation*).

        Args:
            on_progress (ProgressCallback): Called with every progress update.

        Returns:
            CalibrationResult: The outcome, with the raw offset reported by the meter.

        Raises:
            CalibrationError: If the meter refuses, fails or doesn't answer.
        """
        client = self.client
        if client is None:
            raise CalibrationError("The power meter is not connected.")
        on_progress(
            CalibrationProgress(
                CalibrationPhase.MEASURING, "Measuring the zero offset: keep the cranks still…"
            )
        )
        await self._subscribe_control_point(client)
        torque_based = await self._read_torque_based(client)
        while not self._responses.empty():
            self._responses.get_nowait()
        await client.write_gatt_char(
            uuids.CYCLING_POWER_CONTROL_POINT, cmd_start_offset_compensation(), response=True
        )
        resp = await self._await_offset_response()
        if resp.result == ResultCode.NOT_SUPPORTED:
            raise CalibrationError("This power meter does not support zero-offset calibration.")
        if not resp.ok:
            raise CalibrationError(
                f"Calibration failed ({result_text(resp.result)}). Unclip, keep the cranks "
                "still with no weight on the pedals, and try again."
            )
        offset = parse_offset(resp.params)
        if offset is None:
            return CalibrationResult("Zero offset calibration complete.")
        return CalibrationResult(
            f"Zero offset calibration complete: offset {format_offset(offset, torque_based)}.",
            offset,
        )
