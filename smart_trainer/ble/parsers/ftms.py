"""FTMS (Fitness Machine Service) parsers and commands.

Indoor bike data, control point, spin-down and machine status.

Pure functions, free of side effects, so they can be unit tested with captured payloads.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass

from smart_trainer.ble.parsers.control import ControlResponse


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


_Decoder = Callable[[bytes, int], float | int]


# Indoor Bike Data fields after instantaneous speed, in payload order:
# (flag bit, IndoorBikeData attribute or None to skip, size in bytes, decoder(data, offset))
_IBD_FIELDS: tuple[tuple[int, str | None, int, _Decoder | None], ...] = (
    (0x0002, None, 2, None),  # average speed
    (0x0004, "cadence_rpm", 2, lambda d, i: struct.unpack_from("<H", d, i)[0] / 2.0),  # 0.5 rpm
    (0x0008, None, 2, None),  # average cadence
    (0x0010, "distance_m", 3, lambda d, i: int.from_bytes(d[i : i + 3], "little")),  # uint24
    (0x0020, "resistance_level", 2, lambda d, i: struct.unpack_from("<h", d, i)[0]),  # sint16
    (0x0040, "power", 2, lambda d, i: struct.unpack_from("<h", d, i)[0]),  # sint16
    (0x0080, None, 2, None),  # average power
    (0x0100, None, 5, None),  # expended energy: total(2) per hour(2) per minute(1)
    (0x0200, "heart_rate", 1, lambda d, i: d[i]),  # uint8
)


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
    for flag, attr, size, decode in _IBD_FIELDS:
        if not flags & flag:
            continue
        if attr is not None and decode is not None:
            setattr(out, attr, decode(data, i))
        i += size
    return out


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
