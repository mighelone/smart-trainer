"""Heart Rate Measurement (0x2A37) parser.

Pure functions, free of side effects, so they can be unit tested with captured payloads.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(slots=True)
class HeartRate:
    """Decoded Heart Rate Measurement (0x2A37).

    Attributes:
        bpm (int): Heart rate in beats per minute.
        rr_intervals_ms (list[float]): RR intervals in milliseconds, if present.
    """

    bpm: int
    rr_intervals_ms: list[float]


def parse_heart_rate(data: bytes) -> HeartRate:
    """Parse a Heart Rate Measurement (0x2A37) payload.

    Args:
        data (bytes): Raw characteristic value.

    Returns:
        HeartRate: Decoded heart rate and RR intervals.
    """
    flags = data[0]
    i = 1
    if flags & 0x01:
        bpm = struct.unpack_from("<H", data, i)[0]
        i += 2
    else:
        bpm = data[i]
        i += 1
    if flags & 0x08:  # energy expended present
        i += 2
    rr: list[float] = []
    if flags & 0x10:
        while i + 1 < len(data):
            rr.append(struct.unpack_from("<H", data, i)[0] * 1000.0 / 1024.0)
            i += 2
    return HeartRate(bpm=bpm, rr_intervals_ms=rr)
