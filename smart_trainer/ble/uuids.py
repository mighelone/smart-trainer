"""Standard Bluetooth SIG UUIDs used by the app."""


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

# Characteristics
INDOOR_BIKE_DATA = sig(0x2AD2)
FTMS_CONTROL_POINT = sig(0x2AD9)
FTMS_STATUS = sig(0x2ADA)
CYCLING_POWER_MEASUREMENT = sig(0x2A63)
HEART_RATE_MEASUREMENT = sig(0x2A37)
CSC_MEASUREMENT = sig(0x2A5B)
