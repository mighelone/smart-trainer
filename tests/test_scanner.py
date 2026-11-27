"""Matching BLE advertisements to drivers."""

import pytest

from smart_trainer.ble.device import DriverId
from smart_trainer.ble.scanner import discovered
from smart_trainer.ble.uuids import CYCLING_POWER_SERVICE, FTMS_SERVICE, HEART_RATE_SERVICE

SPEAKER = "0000110b-0000-1000-8000-00805f9b34fb"  # audio sink: no driver handles it


@pytest.mark.parametrize(
    ("services", "drivers", "roles"),
    [
        ([HEART_RATE_SERVICE], [DriverId.HEART_RATE], {"hr"}),
        ([CYCLING_POWER_SERVICE], [DriverId.CYCLING_POWER], {"power", "cadence"}),
        ([SPEAKER, HEART_RATE_SERVICE], [DriverId.HEART_RATE], {"hr"}),
    ],
)
def test_advertisement_is_matched_to_its_drivers(services, drivers, roles):
    found = discovered("A", "Device", -60, services)
    assert found is not None
    assert found.drivers == drivers
    assert found.roles >= roles


def test_trainer_matches_ftms_first():
    found = discovered("A", "Zwift Hub", -50, [CYCLING_POWER_SERVICE, FTMS_SERVICE])
    assert found is not None and found.drivers[0] == DriverId.FTMS


def test_unknown_devices_are_ignored():
    assert discovered("A", "Speaker", -60, [SPEAKER]) is None
