"""Driver for BLE Heart Rate Service monitors."""

from __future__ import annotations

from bleak import BleakClient

from smart_trainer.ble import uuids
from smart_trainer.ble.bleak_device import BleakDevice
from smart_trainer.ble.device import register
from smart_trainer.ble.parsers import parse_heart_rate
from smart_trainer.core.state import Reading, Role


@register
class HeartRateMonitor(BleakDevice):
    """Any BLE heart-rate strap (Garmin HRM-Pro/Dual, Polar H10, Wahoo TICKR, ...)."""

    DRIVER = "heart_rate"
    SERVICE_UUID = uuids.HEART_RATE_SERVICE
    ROLES = frozenset({Role.HR})

    async def on_connected(self, client: BleakClient) -> None:
        """Subscribe to Heart Rate Measurement notifications.

        Args:
            client (BleakClient): The connected bleak client.
        """

        def handle(_char, data: bytearray) -> None:
            """Parse a Heart Rate Measurement notification and emit a reading.

            Args:
                _char (BleakGATTCharacteristic): Characteristic that sent the notification.
                data (bytearray): Raw notification payload.
            """
            hr = parse_heart_rate(bytes(data))
            if hr.bpm > 0:
                self.emit(Reading(self.id, hr=hr.bpm))

        await client.start_notify(uuids.HEART_RATE_MEASUREMENT, handle)
