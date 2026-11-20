"""Cycling Power Service parsers and commands.

Measurement, cadence from crank revolutions, offset compensation and features.

Pure functions, free of side effects, so they can be unit tested with captured payloads.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from smart_trainer.ble.parsers.control import ControlResponse


@dataclass(slots=True)
class CyclingPower:
    """Decoded Cycling Power Measurement (0x2A63).

    Attributes:
        power (int): Instantaneous power in watts.
        balance_left_pct (float | None): Percentage contributed by the left pedal.
        crank_revs (int | None): Cumulative crank revolutions (uint16).
        crank_event_time (int | None): Last crank event time in 1/1024 s (uint16).
    """

    power: int
    balance_left_pct: float | None = None  # percentage contributed by the LEFT pedal
    crank_revs: int | None = None  # cumulative, uint16
    crank_event_time: int | None = None  # 1/1024 s, uint16


def parse_cycling_power(data: bytes) -> CyclingPower:
    """Parse a Cycling Power Measurement (0x2A63) payload.

    Args:
        data (bytes): Raw characteristic value.

    Returns:
        CyclingPower: Decoded power, pedal balance and crank data.
    """
    flags = struct.unpack_from("<H", data, 0)[0]
    power = struct.unpack_from("<h", data, 2)[0]
    i = 4
    balance = None
    if flags & 0x0001:  # pedal power balance present (1/2 %)
        raw = data[i] / 2.0
        i += 1
        # bit 1 = balance reference: 1 -> value refers to the LEFT pedal, 0 -> unknown
        balance = raw if flags & 0x0002 else None
    if flags & 0x0004:  # accumulated torque
        i += 2
    if flags & 0x0010:  # wheel revolution data
        i += 6
    crank_revs = crank_time = None
    if flags & 0x0020:  # crank revolution data
        crank_revs, crank_time = struct.unpack_from("<HH", data, i)
        i += 4
    return CyclingPower(
        power=power, balance_left_pct=balance, crank_revs=crank_revs, crank_event_time=crank_time
    )


class CadenceCalculator:
    """Derives cadence (rpm) from cumulative crank revolutions / event time (uint16 rollover)."""

    def __init__(self, stale_after_s: float = 3.0) -> None:
        """Initialize the calculator.

        Args:
            stale_after_s (float): Seconds without a new crank event after which
                cadence drops to zero.
        """
        self._last: tuple[int, int] | None = None
        self._cadence = 0.0
        self._idle_s = 0.0
        self._stale_after_s = stale_after_s

    def update(self, revs: int, event_time: int, dt_s: float = 1.0) -> float:
        """Update the cadence with a new crank revolution sample.

        Args:
            revs (int): Cumulative crank revolutions (uint16).
            event_time (int): Last crank event time in 1/1024 s (uint16).
            dt_s (float): Seconds elapsed since the previous update.

        Returns:
            float: Current cadence in rpm.
        """
        if self._last is None:
            self._last = (revs, event_time)
            return 0.0
        last_revs, last_time = self._last
        d_revs = (revs - last_revs) & 0xFFFF
        d_time = (event_time - last_time) & 0xFFFF
        if d_revs == 0 or d_time == 0:
            # No new crank event: keep last value briefly, then decay to zero
            self._idle_s += dt_s
            if self._idle_s >= self._stale_after_s:
                self._cadence = 0.0
            return self._cadence
        self._idle_s = 0.0
        self._last = (revs, event_time)
        rpm = d_revs * 60.0 * 1024.0 / d_time
        if rpm < 250:  # reject glitches
            self._cadence = rpm
        return self._cadence


class CpOp:
    """Cycling Power Control Point (0x2A66) op codes used by the app."""

    START_OFFSET_COMPENSATION = 0x0C
    RESPONSE = 0x20


def cmd_start_offset_compensation() -> bytes:
    """Build the Cycling Power *Start Offset Compensation* (zero offset) command.

    Returns:
        bytes: Encoded control point command.
    """
    return bytes([CpOp.START_OFFSET_COMPENSATION])


def parse_cp_control_response(data: bytes) -> ControlResponse | None:
    """Parse a Cycling Power control point response indication.

    Args:
        data (bytes): Raw characteristic value.

    Returns:
        ControlResponse | None: The response (result codes as for FTMS, 0x05 unused),
        or ``None`` if the payload is not a response.
    """
    if len(data) >= 3 and data[0] == CpOp.RESPONSE:
        return ControlResponse(request_op=data[1], result=data[2], params=bytes(data[3:]))
    return None


def parse_offset(params: bytes) -> int | None:
    """Parse the offset returned with a successful *Start Offset Compensation*.

    Args:
        params (bytes): Response parameters (sint16; N for force-based meters,
            1/32 N·m for torque-based ones).

    Returns:
        int | None: The raw offset, or ``None`` if the meter sent none.
    """
    if len(params) < 2:
        return None
    return struct.unpack_from("<h", params, 0)[0]


@dataclass(slots=True)
class CpFeature:
    """Decoded Cycling Power Feature (0x2A65), only the bits the app uses.

    Attributes:
        offset_compensation (bool): Whether *Start Offset Compensation* is supported.
        torque_based (bool): Whether the meter measures torque (else force).
    """

    offset_compensation: bool
    torque_based: bool


def parse_cp_feature(data: bytes) -> CpFeature:
    """Parse a Cycling Power Feature (0x2A65) value.

    Args:
        data (bytes): Raw characteristic value (uint32 flags).

    Returns:
        CpFeature: Offset compensation support and measurement context.
    """
    flags = int.from_bytes(data[:4], "little")
    return CpFeature(
        offset_compensation=bool(flags & (1 << 9)), torque_based=bool(flags & (1 << 16))
    )


def format_offset(offset: int, torque_based: bool | None) -> str:
    """Format a raw zero offset with its unit, when known.

    Args:
        offset (int): Raw offset from :func:`parse_offset`.
        torque_based (bool | None): Measurement context from the feature, if read.

    Returns:
        str: E.g. ``"512 (16.00 N·m)"`` or ``"512"``.
    """
    if torque_based is None:
        return str(offset)
    if torque_based:
        return f"{offset} ({offset / 32:.2f} N·m)"
    return f"{offset} N"
