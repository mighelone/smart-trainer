"""Interval cues: announced once per step change, CUE_LEAD_S before it."""

import json
import shutil
import subprocess

import pytest

from smart_trainer.config import Settings
from smart_trainer.core.engine import Engine
from smart_trainer.core.ride import CUE_LEAD_S
from smart_trainer.core.state import Snapshot
from smart_trainer.core.workout import Step, Workout
from smart_trainer.ui.widgets.cues import BEEP_JS
from smart_trainer.ui.widgets.text import next_step_text

T0 = 1_700_000_000
RIDING = Snapshot(power=200, cadence=90, hr=140)
THREE_STEPS = Workout("T", [Step(20, 0.5), Step(20, 0.8, text="Tempo"), Step(20, 0.6)])


@pytest.fixture
def ride(tmp_path):
    e = Engine.from_settings(Settings(ftp=200, auto_upload=False), sessions=tmp_path)
    yield e.ride
    e.ride.close()


def _cue_times(ride, ticks: int) -> list[tuple[float, int]]:
    """Ride ``ticks`` seconds; return (workout elapsed, ending step) at each new cue."""
    out, seq = [], ride.cue.seq if ride.cue else 0
    for i in range(ticks):
        ride.tick(RIDING, T0 + i)
        if ride.cue and ride.cue.seq != seq:
            seq = ride.cue.seq
            out.append((ride.runner.elapsed, ride.cue.step_index))
    return out


def test_one_cue_per_step_change_five_seconds_before(ride):
    ride.start(THREE_STEPS)
    assert _cue_times(ride, 60) == [(20 - CUE_LEAD_S, 0), (40 - CUE_LEAD_S, 1)]
    cue = ride.cue
    assert cue is not None and cue.in_s == CUE_LEAD_S and cue.next_step.start == 0.6
    # the last step ends the workout: nothing to announce


def test_cue_describes_the_next_step(ride):
    ride.start(THREE_STEPS)
    _cue_times(ride, 15)
    assert ride.cue is not None and ride.cue.next_step.text == "Tempo"


def test_no_cue_while_paused(ride):
    ride.start(THREE_STEPS)
    _cue_times(ride, 14)  # elapsed 14: one second before the cue
    ride.toggle_pause()
    assert _cue_times(ride, 5) == []  # the workout clock stops, nothing announced
    ride.toggle_pause()
    assert _cue_times(ride, 1) == [(15, 0)]


def test_skipping_a_step_cues_the_new_one(ride):
    ride.start(THREE_STEPS)
    _cue_times(ride, 3)
    ride.skip_step()  # straight into step 1 (elapsed 20)
    assert _cue_times(ride, 20) == [(35, 1)]


def test_a_short_step_is_cued_when_it_starts(ride):
    ride.start(Workout("T", [Step(10, 0.5), Step(3, 1.5), Step(10, 0.5)]))
    assert _cue_times(ride, 20) == [(5, 0), (10, 1)]  # step 1 (3 s) is cued as it starts


def test_free_ride_has_no_cues(ride):
    ride.start(None)
    for i in range(30):
        ride.tick(RIDING, T0 + i)
    assert ride.cue is None


def test_next_step_text():
    assert next_step_text(Step(30, 2.0, text="Sprint"), 200) == "Sprint · 400 W for 00:30"
    assert next_step_text(Step(30, 2.0), 200, intensity=1.1) == "440 W for 00:30"
    assert next_step_text(Step(300, 0.0, free_ride=True), 200) == "Free ride for 05:00"


NODE_HARNESS = """
const made = [], listeners = [], gains = [];
class FakeAudioContext {
  constructor() { this.state = "suspended"; this.currentTime = 1; this.destination = {}; }
  resume() { this.state = "running"; }
  createOscillator() {
    const o = {frequency: {}, connect: (g) => g};
    o.start = (t) => (o.at = t);
    o.stop = (t) => (o.end = t);
    made.push(o);
    return o;
  }
  createGain() {
    const peaks = [];
    gains.push(peaks);
    const record = (v) => peaks.push(v);
    const gain = {setValueAtTime: record, exponentialRampToValueAtTime: record};
    return {gain, connect: (d) => d};
  }
}
globalThis.window = globalThis;
window.AudioContext = FakeAudioContext;
globalThis.document = {addEventListener: (ev) => listeners.push(ev)};
%(js)s
const first = window.smartTrainerBeep;
%(js)s  // defined again by another page section: must not reinstall
window.smartTrainerBeep(2);
console.log(JSON.stringify({
  same: first === window.smartTrainerBeep, listeners, made: made.map((o) => [o.at, o.end]),
  freq: made.map((o) => o.frequency.value), type: made.map((o) => o.type),
  peak: Math.max(...gains.flat().filter((v) => typeof v === "number")),
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to run the script")
def test_beep_script_in_javascript():
    out = subprocess.run(
        ["node", "-e", NODE_HARNESS % {"js": BEEP_JS}], capture_output=True, text=True, check=True
    )
    result = json.loads(out.stdout)
    assert result["same"] and result["listeners"] == ["pointerdown", "keydown"]
    assert result["freq"] == [880, 880] and result["type"] == ["square", "square"]
    assert result["peak"] == 0.8  # loud enough to hear over a video
    [(a0, a1), (b0, b1)] = result["made"]  # two 0.2 s beeps, 0.3 s apart
    assert b0 - a0 == pytest.approx(0.3) and a1 - a0 == pytest.approx(0.21)
