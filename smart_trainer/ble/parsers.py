"""Pure parsers for standard BLE fitness characteristics (bytes -> dataclasses).

All functions are side-effect free so they can be unit tested with captured payloads.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


# --------------------------------------------------------------------------- HR
@dataclass(slots=True)
class HeartRate:
    bpm: int
    rr_intervals_ms: list[float]


def parse_heart_rate(data: bytes) -> HeartRate:
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
    power: int
    balance_left_pct: float | None = None  # percentage contributed by the LEFT pedal
    crank_revs: int | None = None  # cumulative, uint16
    crank_event_time: int | None = None  # 1/1024 s, uint16


def parse_cycling_power(data: bytes) -> CyclingPower:
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
        self._last: tuple[int, int] | None = None
        self._cadence = 0.0
        self._idle_s = 0.0
        self._stale_after_s = stale_after_s

    def update(self, revs: int, event_time: int, dt_s: float = 1.0) -> float:
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
    speed_kmh: float | None = None
    cadence_rpm: float | None = None
    distance_m: int | None = None
    resistance_level: int | None = None
    power: int | None = None
    heart_rate: int | None = None


def parse_indoor_bike_data(data: bytes) -> IndoorBikeData:
    """FTMS Indoor Bike Data (0x2AD2). Fields are present depending on flag bits."""
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
    REQUEST_CONTROL = 0x00
    RESET = 0x01
    SET_TARGET_RESISTANCE = 0x04
    SET_TARGET_POWER = 0x05
    START_RESUME = 0x07
    STOP_PAUSE = 0x08
    SET_SIMULATION = 0x11
    RESPONSE = 0x80


def cmd_request_control() -> bytes:
    return bytes([FtmsOp.REQUEST_CONTROL])


def cmd_start() -> bytes:
    return bytes([FtmsOp.START_RESUME])


def cmd_set_target_power(watts: int) -> bytes:
    watts = max(0, min(round(watts), 2000))
    return struct.pack("<Bh", FtmsOp.SET_TARGET_POWER, watts)


def cmd_set_target_resistance(level_pct: float) -> bytes:
    """Resistance as 0-100 %. FTMS spec unit is 0.1 (uint8 in v1.0, many trainers accept 0-200)."""
    level = max(0.0, min(level_pct, 100.0))
    return struct.pack("<BB", FtmsOp.SET_TARGET_RESISTANCE, round(level * 2) & 0xFF)


def cmd_set_simulation(
    grade_pct: float, wind_mps: float = 0.0, crr: float = 0.004, cw: float = 0.51
) -> bytes:
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
    request_op: int
    # 0x01 success, 0x02 not supported, 0x03 invalid param, 0x04 failed, 0x05 not permitted
    result: int

    @property
    def ok(self) -> bool:
        return self.result == 0x01


def parse_control_response(data: bytes) -> ControlResponse | None:
    if len(data) >= 3 and data[0] == FtmsOp.RESPONSE:
        return ControlResponse(request_op=data[1], result=data[2])
    return None
