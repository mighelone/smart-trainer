"""Bluetooth UUIDs used by the app: standard SIG ones, plus Zwift's own."""


def sig(short: int) -> str:
    """Expand a 16-bit Bluetooth SIG UUID to its full 128-bit form.

    Args:
        short (int): 16-bit assigned number (e.g. ``0x1826``).

    Returns:
        str: The full lowercase 128-bit UUID string.
    """
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


# Services
FTMS_SERVICE = sig(0x1826)
CYCLING_POWER_SERVICE = sig(0x1818)
HEART_RATE_SERVICE = sig(0x180D)
CSC_SERVICE = sig(0x1816)
ZWIFT_SERVICE = sig(0xFC82)  # Zwift controllers (Click v2, Ride) with current firmware

# Characteristics
INDOOR_BIKE_DATA = sig(0x2AD2)
FTMS_CONTROL_POINT = sig(0x2AD9)
FTMS_STATUS = sig(0x2ADA)
FTMS_FEATURE = sig(0x2ACC)
CYCLING_POWER_MEASUREMENT = sig(0x2A63)
CYCLING_POWER_FEATURE = sig(0x2A65)
CYCLING_POWER_CONTROL_POINT = sig(0x2A66)
HEART_RATE_MEASUREMENT = sig(0x2A37)
CSC_MEASUREMENT = sig(0x2A5B)
# Zwift controller characteristics (not documented by Zwift; see ble/parsers/zwift.py)
ZWIFT_ASYNC = "00000002-19ca-4651-86e5-fa29dcdd09d1"  # notify: button and status messages
ZWIFT_SYNC_RX = "00000003-19ca-4651-86e5-fa29dcdd09d1"  # write: the "RideOn" handshake
ZWIFT_SYNC_TX = "00000004-19ca-4651-86e5-fa29dcdd09d1"  # indicate: the handshake reply
