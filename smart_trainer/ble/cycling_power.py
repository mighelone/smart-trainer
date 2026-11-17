"""Driver for BLE Cycling Power Service meters."""

from __future__ import annotations

import time

from bleak import BleakClient

from smart_trainer.ble import uuids
from smart_trainer.ble.bleak_device import BleakDevice
from smart_trainer.ble.device import DriverId, register
from smart_trainer.ble.parsers import CadenceCalculator, parse_cycling_power
from smart_trainer.core.state import Reading, Role


@register
class PowerMeter(BleakDevice):
    """Any BLE Cycling Power meter (Favero Assioma, Garmin Rally, Stages, Quarq, ...).

    Provides power, cadence (derived from crank revolutions) and L/R balance.
    """

    DRIVER = DriverId.CYCLING_POWER
    SERVICE_UUID = uuids.CYCLING_POWER_SERVICE
    ROLES = frozenset({Role.POWER, Role.CADENCE})
    PRIORITY = 5

    async def on_connected(self, client: BleakClient) -> None:
        """Subscribe to Cycling Power Measurement notifications.

        Args:
            client (BleakClient): The connected bleak client.
        """
        cadence = CadenceCalculator()
        last = [time.monotonic()]

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
