import struct

from smart_trainer.ble.parsers.control import result_text
from smart_trainer.ble.parsers.cycling_power import (
    CadenceCalculator,
    CpOp,
    cmd_start_offset_compensation,
    format_offset,
    parse_cp_control_response,
    parse_cp_feature,
    parse_cycling_power,
    parse_offset,
)
from smart_trainer.ble.parsers.ftms import (
    FtmsOp,
    FtmsStatus,
    SpinDownStatus,
    cmd_set_simulation,
    cmd_set_target_power,
    cmd_spin_down,
    ftms_spin_down_supported,
    parse_control_response,
    parse_indoor_bike_data,
    parse_machine_status,
    parse_spin_down_targets,
)
from smart_trainer.ble.parsers.heart_rate import parse_heart_rate


def test_heart_rate_uint8_and_rr():
    hr = parse_heart_rate(bytes([0x10, 72, 0x00, 0x04]))  # rr = 1024/1024 s
    assert hr.bpm == 72
    assert hr.rr_intervals_ms == [1000.0]


def test_heart_rate_uint16():
    assert parse_heart_rate(bytes([0x01]) + struct.pack("<H", 301)).bpm == 301


def test_cycling_power_with_balance_and_crank():
    flags = 0x0001 | 0x0002 | 0x0020
    data = struct.pack("<Hh", flags, 250) + bytes([98]) + struct.pack("<HH", 1000, 2048)
    measurement = parse_cycling_power(data)
    assert measurement.power == 250
    assert measurement.balance_left_pct == 49.0
    assert (measurement.crank_revs, measurement.crank_event_time) == (1000, 2048)


def test_cadence_with_rollover():
    calculator = CadenceCalculator()
    calculator.update(65535, 65000)
    # 1.5 revs/s -> 90 rpm: +3 revs in 2 s (2048 ticks) across both rollovers
    rpm = calculator.update((65535 + 3) & 0xFFFF, (65000 + 2048) & 0xFFFF)
    assert abs(rpm - 90) < 0.01


def test_cadence_decays_when_idle():
    calculator = CadenceCalculator(stale_after_s=2)
    calculator.update(10, 0)
    calculator.update(11, 683)
    assert calculator.update(11, 683) > 0
    assert calculator.update(11, 683) == 0.0


def test_indoor_bike_data_zwift_hub_style():
    flags = 0x0004 | 0x0040  # speed present (bit0=0), cadence, power
    data = struct.pack("<HHHh", flags, 3250, 180, 210)
    bike = parse_indoor_bike_data(data)
    assert bike.speed_kmh == 32.5 and bike.cadence_rpm == 90 and bike.power == 210


def test_indoor_bike_data_with_distance_and_resistance():
    flags = 0x0010 | 0x0020 | 0x0040
    data = (
        struct.pack("<HH", flags, 1000)
        + (12345).to_bytes(3, "little")
        + struct.pack("<hh", 30, 180)
    )
    bike = parse_indoor_bike_data(data)
    assert bike.distance_m == 12345 and bike.resistance_level == 30 and bike.power == 180


def test_commands():
    assert cmd_set_target_power(215) == bytes([0x05, 215, 0])
    sim = cmd_set_simulation(5.0)
    assert sim[0] == 0x11 and struct.unpack_from("<h", sim, 3)[0] == 500
    response = parse_control_response(bytes([0x80, 0x05, 0x01]))
    assert response and response.ok and response.request_op == 0x05


def test_spin_down_command_and_response_with_targets():
    assert cmd_spin_down() == bytes([0x13, 0x01])
    assert cmd_spin_down(start=False) == bytes([0x13, 0x02])
    # success + target speed low 32.00 km/h, high 36.50 km/h
    response = parse_control_response(bytes([0x80, 0x13, 0x01]) + struct.pack("<HH", 3200, 3650))
    assert response and response.ok and response.request_op == FtmsOp.SPIN_DOWN
    assert parse_spin_down_targets(response.params) == (32.0, 36.5)


def test_spin_down_refused_has_no_targets():
    response = parse_control_response(bytes([0x80, 0x13, 0x02]))
    assert response and not response.ok and response.params == b""
    assert parse_spin_down_targets(response.params) is None
    assert result_text(response.result) == "not supported"
    assert result_text(0x7F) == "error 0x7f"


def test_machine_status_spin_down():
    st = parse_machine_status(bytes([0x14, 0x04]))
    assert st and st.op == FtmsStatus.SPIN_DOWN_STATUS
    assert st.params[0] == SpinDownStatus.STOP_PEDALING
    lost = parse_machine_status(bytes([0xFF]))
    assert lost and lost.op == FtmsStatus.CONTROL_LOST
    assert parse_machine_status(b"") is None


def test_ftms_feature_spin_down_bit():
    machine = 0x00004000  # power measurement supported
    assert ftms_spin_down_supported(struct.pack("<II", machine, 1 << 15 | 1 << 13))
    assert not ftms_spin_down_supported(struct.pack("<II", machine, 1 << 13))
    assert not ftms_spin_down_supported(b"\x00\x00")


def test_cp_offset_compensation_command_and_response():
    assert cmd_start_offset_compensation() == bytes([0x0C])
    response = parse_cp_control_response(bytes([0x20, 0x0C, 0x01]) + struct.pack("<h", -42))
    assert response and response.ok and response.request_op == CpOp.START_OFFSET_COMPENSATION
    assert parse_offset(response.params) == -42
    failed = parse_cp_control_response(bytes([0x20, 0x0C, 0x04]))
    assert failed and not failed.ok and parse_offset(failed.params) is None
    assert parse_cp_control_response(bytes([0x80, 0x0C, 0x01])) is None  # FTMS, not CP


def test_cp_feature_and_offset_format():
    feature = parse_cp_feature(struct.pack("<I", 1 << 9 | 1 << 16 | 0x01))
    assert feature.offset_compensation and feature.torque_based
    feature = parse_cp_feature(struct.pack("<I", 0x0C))
    assert not feature.offset_compensation and not feature.torque_based
    assert format_offset(512, True) == "512 (16.00 N·m)"
    assert format_offset(30, False) == "30 N"
    assert format_offset(7, None) == "7"
