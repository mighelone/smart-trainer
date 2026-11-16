import json
from pathlib import Path

import pytest

from smart_trainer.core.workout import WorkoutRunner, parse_intervals_doc, parse_zwo

DOC = json.loads((Path(__file__).parent / "data" / "cadence_endurance_doc.json").read_text())


def test_parse_intervals_doc():
    wo = parse_intervals_doc(DOC, name="Cadence", ftp=250, source_id="42")
    assert len(wo.steps) == 1 + 6 + 4 + 1
    assert wo.duration_s == 480 + 6 * 360 + 4 * 60 + 300
    warm = wo.steps[0]
    assert warm.start == 0.45 and warm.end == 0.75 and warm.cadence == 85
    assert wo.steps[7].unit == "watts" and wo.steps[7].target_w(0, 250) == 200
    assert wo.steps[8].target_w(0, 250) == 0.65 * 250  # zone 2 midpoint
    assert wo.steps[9].target_w(0, 250) == pytest.approx(0.65 * 250)  # range -> midpoint
    assert wo.steps[10].free_ride and wo.steps[10].target_w(0, 250) is None


def test_runner_ramp_skip_and_intensity():
    wo = parse_intervals_doc(DOC, ftp=200)
    r = WorkoutRunner(wo, ftp=200)
    assert r.target_w() == 90  # 45 % of 200
    r.advance(240)
    assert r.target_w() == pytest.approx(120)  # halfway up the ramp: 60 %
    r.skip()
    assert r.step_index() == 1 and r.target_w() == 140
    r.intensity = 1.1
    assert r.target_w() == pytest.approx(154)
    r.paused = True
    r.advance(30)
    assert r.elapsed == 480


def test_runner_resume_from_elapsed():
    wo = parse_intervals_doc(DOC, ftp=200)
    r = WorkoutRunner(wo, ftp=200, elapsed_s=480 + 400)
    i, step, into, remaining = r.current()
    assert i == 2 and into == 40 and remaining == 320


def test_parse_zwo():
    xml = """<workout_file><name>Test</name><workout>
      <Warmup Duration="300" PowerLow="0.5" PowerHigh="0.75"/>
      <IntervalsT Repeat="2" OnDuration="60" OffDuration="30" OnPower="1.2" OffPower="0.5"/>
      <SteadyState Duration="120" Power="0.8" Cadence="95"/>
      <FreeRide Duration="60"/>
    </workout></workout_file>"""
    wo = parse_zwo(xml)
    assert wo.name == "Test"
    assert [s.duration_s for s in wo.steps] == [300, 60, 30, 60, 30, 120, 60]
    assert wo.steps[5].cadence == 95 and wo.steps[6].free_ride
