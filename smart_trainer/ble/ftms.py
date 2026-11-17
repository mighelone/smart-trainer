"""FTMS smart trainer driver (Zwift Hub, Wahoo KICKR, Tacx Neo, Elite, Saris, ...)."""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient

from smart_trainer.ble import uuids
from smart_trainer.ble.bleak_device import BleakDevice
from smart_trainer.ble.device import ControllableTrainer, register
from smart_trainer.ble.parsers import (
    FtmsOp,
    cmd_request_control,
    cmd_set_simulation,
    cmd_set_target_power,
    cmd_set_target_resistance,
    cmd_start,
    parse_control_response,
    parse_indoor_bike_data,
)
from smart_trainer.core.state import Reading, Role

log = logging.getLogger(__name__)


@register
class FtmsTrainer(BleakDevice, ControllableTrainer):
    """Smart trainer implementing the Fitness Machine Service (FTMS).

    Attributes:
        RESPONSE_TIMEOUT (float): Seconds to wait for a control point response.
        has_control (bool): Whether control of the trainer has been granted.
    """

    DRIVER = "ftms"
    SERVICE_UUID = uuids.FTMS_SERVICE
    ROLES = frozenset({Role.TRAINER, Role.POWER, Role.CADENCE, Role.SPEED})
    PRIORITY = 10
    RESPONSE_TIMEOUT = 3.0

    def __init__(self, address: str, name: str = "") -> None:
        """Initialize the trainer.

        Args:
            address (str): BLE address (or platform UUID on macOS) of the trainer.
            name (str): Human-readable device name; defaults to the address.
        """
        super().__init__(address, name)
        self._responses: asyncio.Queue = asyncio.Queue()
        self._lock = asyncio.Lock()
        self.has_control = False

    async def on_connected(self, client: BleakClient) -> None:
        """Subscribe to bike data and control point notifications, then take control.

        Args:
            client (BleakClient): The connected bleak client.
        """
        self.has_control = False
        self._responses = asyncio.Queue()

        def on_bike_data(_char, data: bytearray) -> None:
            """Parse an Indoor Bike Data notification and emit a reading.

            Args:
                _char (BleakGATTCharacteristic): Characteristic that sent the notification.
                data (bytearray): Raw notification payload.
            """
            d = parse_indoor_bike_data(bytes(data))
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

        await client.start_notify(uuids.FTMS_CONTROL_POINT, on_cp)
        await client.start_notify(uuids.INDOOR_BIKE_DATA, on_bike_data)
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
        if r is not None and r.result == 0x05:  # control not permitted -> retake once
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


__all__ = ["FtmsTrainer", "FtmsOp"]
