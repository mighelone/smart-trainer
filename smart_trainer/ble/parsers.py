"""Pure parsers for standard BLE fitness characteristics (bytes -> dataclasses).

All functions are side-effect free so they can be unit tested with captured payloads.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum


# --------------------------------------------------------------------------- HR
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


# ------------------------------------------------------------- Cycling power
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


# ------------------------------------------------------------ Indoor bike data
@dataclass(slots=True)
class IndoorBikeData:
    """Decoded FTMS Indoor Bike Data (0x2AD2).

    Attributes:
        speed_kmh (float | None): Instantaneous speed in km/h.
        cadence_rpm (float | None): Instantaneous cadence in rpm.
        distance_m (int | None): Total distance in metres.
        resistance_level (int | None): Current resistance level.
        power (int | None): Instantaneous power in watts.
        heart_rate (int | None): Heart rate in bpm.
    """

    speed_kmh: float | None = None
    cadence_rpm: float | None = None
    distance_m: int | None = None
    resistance_level: int | None = None
    power: int | None = None
    heart_rate: int | None = None


def parse_indoor_bike_data(data: bytes) -> IndoorBikeData:
    """Parse an FTMS Indoor Bike Data (0x2AD2) payload.

    Fields are present depending on flag bits.

    Args:
        data (bytes): Raw characteristic value.

    Returns:
        IndoorBikeData: Decoded fields; absent fields are ``None``.
    """
    flags = struct.unpack_from("<H", data, 0)[0]
    i = 2
    out = IndoorBikeData()
    if not flags & 0x0001:  # bit0 == 0 means instantaneous speed IS present
        out.speed_kmh = struct.unpack_from("<H", data, i)[0] / 100.0
        i += 2
    if flags & 0x0002:  # average speed
        i += 2
    if flags & 0x0004:  # instantaneous cadence (0.5 rpm)
        out.cadence_rpm = struct.unpack_from("<H", data, i)[0] / 2.0
        i += 2
    if flags & 0x0008:  # average cadence
        i += 2
    if flags & 0x0010:  # total distance uint24
        out.distance_m = int.from_bytes(data[i : i + 3], "little")
        i += 3
    if flags & 0x0020:  # resistance level sint16
        out.resistance_level = struct.unpack_from("<h", data, i)[0]
        i += 2
    if flags & 0x0040:  # instantaneous power sint16
        out.power = struct.unpack_from("<h", data, i)[0]
        i += 2
    if flags & 0x0080:  # average power
        i += 2
    if flags & 0x0100:  # expended energy: total(2) per hour(2) per minute(1)
        i += 5
    if flags & 0x0200:  # heart rate uint8
        out.heart_rate = data[i]
        i += 1
    return out


# ------------------------------------------------------- FTMS control point
class FtmsOp:
    """FTMS Control Point (0x2AD9) op codes."""

    REQUEST_CONTROL = 0x00
    RESET = 0x01
    SET_TARGET_RESISTANCE = 0x04
    SET_TARGET_POWER = 0x05
    START_RESUME = 0x07
    STOP_PAUSE = 0x08
    SET_SIMULATION = 0x11
    SPIN_DOWN = 0x13
    RESPONSE = 0x80


class ResultCode(IntEnum):
    """FTMS / Cycling Power control point result codes."""

    SUCCESS = 0x01
    NOT_SUPPORTED = 0x02
    INVALID_PARAMETER = 0x03
    FAILED = 0x04
    CONTROL_NOT_PERMITTED = 0x05


def cmd_request_control() -> bytes:
    """Build the FTMS *Request Control* command.

    Returns:
        bytes: Encoded control point command.
    """
    return bytes([FtmsOp.REQUEST_CONTROL])


def cmd_start() -> bytes:
    """Build the FTMS *Start or Resume* command.

    Returns:
        bytes: Encoded control point command.
    """
    return bytes([FtmsOp.START_RESUME])


def cmd_spin_down(start: bool = True) -> bytes:
    """Build the FTMS *Spin Down Control* command.

    Args:
        start (bool): ``True`` to start a spin-down, ``False`` to ignore one the
            trainer requested.

    Returns:
        bytes: Encoded control point command.
    """
    return bytes([FtmsOp.SPIN_DOWN, 0x01 if start else 0x02])


def cmd_set_target_power(watts: int) -> bytes:
    """Build the FTMS *Set Target Power* (ERG) command.

    Args:
        watts (int): Target power in watts, clamped to 0-2000.

    Returns:
        bytes: Encoded control point command.
    """
    watts = max(0, min(round(watts), 2000))
    return struct.pack("<Bh", FtmsOp.SET_TARGET_POWER, watts)


def cmd_set_target_resistance(level_pct: float) -> bytes:
    """Build the FTMS *Set Target Resistance Level* command.

    The FTMS spec unit is 0.1 (uint8 in v1.0, many trainers accept 0-200).

    Args:
        level_pct (float): Resistance as 0-100 %.

    Returns:
        bytes: Encoded control point command.
    """
    level = max(0.0, min(level_pct, 100.0))
    return struct.pack("<BB", FtmsOp.SET_TARGET_RESISTANCE, round(level * 2) & 0xFF)


def cmd_set_simulation(
    grade_pct: float, wind_mps: float = 0.0, crr: float = 0.004, cw: float = 0.51
) -> bytes:
    """Build the FTMS *Set Indoor Bike Simulation Parameters* command.

    Args:
        grade_pct (float): Road grade in percent, clamped to ±20.
        wind_mps (float): Wind speed in m/s.
        crr (float): Rolling resistance coefficient.
        cw (float): Wind resistance coefficient in kg/m.

    Returns:
        bytes: Encoded control point command.
    """
    grade_pct = max(-20.0, min(grade_pct, 20.0))
    return struct.pack(
        "<BhhBB",
        FtmsOp.SET_SIMULATION,
        round(wind_mps * 1000),
        round(grade_pct * 100),
        round(crr * 10000) & 0xFF,
        round(cw * 100) & 0xFF,
    )


@dataclass(slots=True)
class ControlResponse:
    """Response to an FTMS control point command.

    Attributes:
        request_op (int): Op code of the command being answered.
        result (int): Result code, see ``ResultCode``.
        params (bytes): Response parameters following the result code, if any.
    """

    request_op: int
    result: int
    params: bytes = b""

    @property
    def ok(self) -> bool:
        """Whether the command succeeded.

        Returns:
            bool: ``True`` if the result code is ``ResultCode.SUCCESS``.
        """
        return self.result == ResultCode.SUCCESS


def parse_control_response(data: bytes) -> ControlResponse | None:
    """Parse an FTMS control point response indication.

    Args:
        data (bytes): Raw characteristic value.

    Returns:
        ControlResponse | None: The response, or ``None`` if the payload is
        not a response.
    """
    if len(data) >= 3 and data[0] == FtmsOp.RESPONSE:
        return ControlResponse(request_op=data[1], result=data[2], params=bytes(data[3:]))
    return None


RESULT_TEXT: dict[int, str] = {
    ResultCode.SUCCESS: "success",
    ResultCode.NOT_SUPPORTED: "not supported",
    ResultCode.INVALID_PARAMETER: "invalid parameter",
    ResultCode.FAILED: "operation failed",
    ResultCode.CONTROL_NOT_PERMITTED: "control not permitted",
}


def result_text(code: int) -> str:
    """Describe an FTMS / Cycling Power control point result code.

    Args:
        code (int): Result code from a control point response.

    Returns:
        str: Human-readable description.
    """
    return RESULT_TEXT.get(code, f"error 0x{code:02x}")


def parse_spin_down_targets(params: bytes) -> tuple[float, float] | None:
    """Parse the target speeds returned with a successful *Spin Down Start*.

    Args:
        params (bytes): Response parameters (two uint16 in 0.01 km/h).

    Returns:
        tuple[float, float] | None: ``(low, high)`` target speed in km/h, or
        ``None`` if the trainer sent none.
    """
    if len(params) < 4:
        return None
    low, high = struct.unpack_from("<HH", params, 0)
    return low / 100.0, high / 100.0


def ftms_spin_down_supported(feature: bytes) -> bool:
    """Whether an FTMS Feature (0x2ACC) value advertises spin-down control.

    Args:
        feature (bytes): Raw characteristic value (machine + target setting features).

    Returns:
        bool: ``True`` if bit 15 of the target setting features is set.
    """
    if len(feature) < 8:
        return False
    target_features = struct.unpack_from("<I", feature, 4)[0]
    return bool(target_features & (1 << 15))


# ------------------------------------------------- FTMS machine status
class FtmsStatus:
    """Fitness Machine Status (0x2ADA) op codes used by the app."""

    RESET = 0x01
    STOPPED_BY_USER = 0x02
    STARTED_BY_USER = 0x04
    SPIN_DOWN_STATUS = 0x14
    CONTROL_LOST = 0xFF


class SpinDownStatus:
    """Parameter of a *Spin Down Status* machine status."""

    REQUESTED = 0x01
    SUCCESS = 0x02
    ERROR = 0x03
    STOP_PEDALING = 0x04


@dataclass(slots=True)
class MachineStatus:
    """Decoded Fitness Machine Status notification.

    Attributes:
        op (int): Status op code (see :class:`FtmsStatus`).
        params (bytes): Status parameters.
    """

    op: int
    params: bytes = b""


def parse_machine_status(data: bytes) -> MachineStatus | None:
    """Parse a Fitness Machine Status (0x2ADA) notification.

    Args:
        data (bytes): Raw characteristic value.

    Returns:
        MachineStatus | None: The status, or ``None`` for an empty payload.
    """
    if not data:
        return None
    return MachineStatus(op=data[0], params=bytes(data[1:]))


# ------------------------------------------------ Cycling power control point
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
