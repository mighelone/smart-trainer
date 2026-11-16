import struct

from smart_trainer.ble.parsers import (
    CadenceCalculator,
    cmd_set_simulation,
    cmd_set_target_power,
    parse_control_response,
    parse_cycling_power,
    parse_heart_rate,
    parse_indoor_bike_data,
)


def test_heart_rate_uint8_and_rr():
    hr = parse_heart_rate(bytes([0x10, 72, 0x00, 0x04]))  # rr = 1024/1024 s
    assert hr.bpm == 72
    assert hr.rr_intervals_ms == [1000.0]


def test_heart_rate_uint16():
    assert parse_heart_rate(bytes([0x01]) + struct.pack("<H", 301)).bpm == 301


def test_cycling_power_with_balance_and_crank():
    flags = 0x0001 | 0x0002 | 0x0020
    data = struct.pack("<Hh", flags, 250) + bytes([98]) + struct.pack("<HH", 1000, 2048)
    m = parse_cycling_power(data)
    assert m.power == 250
    assert m.balance_left_pct == 49.0
    assert (m.crank_revs, m.crank_event_time) == (1000, 2048)


def test_cadence_with_rollover():
    c = CadenceCalculator()
    c.update(65535, 65000)
    # 1.5 revs/s -> 90 rpm: +3 revs in 2 s (2048 ticks) across both rollovers
    rpm = c.update((65535 + 3) & 0xFFFF, (65000 + 2048) & 0xFFFF)
    assert abs(rpm - 90) < 0.01


def test_cadence_decays_when_idle():
    c = CadenceCalculator(stale_after_s=2)
    c.update(10, 0)
    c.update(11, 683)
    assert c.update(11, 683) > 0
    assert c.update(11, 683) == 0.0


def test_indoor_bike_data_zwift_hub_style():
    flags = 0x0004 | 0x0040  # speed present (bit0=0), cadence, power
    data = struct.pack("<HHHh", flags, 3250, 180, 210)
    d = parse_indoor_bike_data(data)
    assert d.speed_kmh == 32.5 and d.cadence_rpm == 90 and d.power == 210


def test_indoor_bike_data_with_distance_and_resistance():
    flags = 0x0010 | 0x0020 | 0x0040
    data = (
        struct.pack("<HH", flags, 1000)
        + (12345).to_bytes(3, "little")
        + struct.pack("<hh", 30, 180)
    )
    d = parse_indoor_bike_data(data)
    assert d.distance_m == 12345 and d.resistance_level == 30 and d.power == 180


def test_commands():
    assert cmd_set_target_power(215) == bytes([0x05, 215, 0])
    sim = cmd_set_simulation(5.0)
    assert sim[0] == 0x11 and struct.unpack_from("<h", sim, 3)[0] == 500
    r = parse_control_response(bytes([0x80, 0x05, 0x01]))
    assert r and r.ok and r.request_op == 0x05
