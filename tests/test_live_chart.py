"""Live chart: the ride history is sent once, then only the new points."""

import json
import shutil
import subprocess
from typing import cast

import pytest
from nicegui import ui

from smart_trainer.config import Settings
from smart_trainer.core.engine import Engine
from smart_trainer.core.history import History, HistoryPoint
from smart_trainer.core.state import Snapshot
from smart_trainer.core.workout import Step, Workout
from smart_trainer.ui import widgets
from smart_trainer.ui.widgets import LiveChart

T0 = 1_700_000_000
RIDING = Snapshot(power=200, cadence=90, hr=140)


@pytest.fixture
def ride(tmp_path):
    e = Engine.from_settings(Settings(ftp=200, auto_upload=False), sessions=tmp_path)
    yield e.ride
    e.ride.close()


def _ticks(ride, n, start=0):
    for i in range(start, start + n):
        ride.tick(RIDING, T0 + i)


class FakeChart:
    """The part of ``ui.echart`` that LiveChart uses."""

    id = 7

    def __init__(self):
        self.options = {"series": [{"data": []}, {"data": []}, {"data": []}], "xAxis": {}}
        self.updates = 0
        self.scripts: list[str] = []
        self.client = self

    def update(self):
        self.updates += 1

    def run_javascript(self, code):
        self.scripts.append(code)

    def sent(self) -> dict:
        """The payload of the last script."""
        code = self.scripts[-1]
        return json.loads(code[code.index("const d = ") + 10 : code.index(";\n  const series")])


def _live(chart: FakeChart, history: History, free_ride: bool) -> LiveChart:
    return LiveChart(cast(ui.echart, chart), history, free_ride=free_ride)


def _add(history: History, xs, power=200.0, hr=140):
    for x in xs:
        history.add(HistoryPoint(x, power, hr, 90.0, None))


# ----------------------------------------------------------------- Ride history
def test_history_x_follows_the_workout_clock(ride):
    ride.start(Workout("T", [Step(60, 1.0), Step(60, 0.5)]))
    _ticks(ride, 2)
    ride.skip_step()
    _ticks(ride, 1, start=2)
    assert [p.x for p in ride.history] == [1, 2, 61]  # the trace jumps with the skip


def test_resume_restores_history_on_the_recorded_clock(tmp_path):
    e = Engine.from_settings(Settings(ftp=200, auto_upload=False), sessions=tmp_path)
    e.ride.start(Workout("T", [Step(600, 1.0)]))
    _ticks(e.ride, 4)
    e.ride.close()
    e2 = Engine.from_settings(Settings(ftp=200, auto_upload=False), sessions=tmp_path)
    [info] = e2.ride.recoverable_sessions()
    e2.ride.resume(info)
    assert [p.x for p in e2.ride.history] == [1, 2, 3, 4]
    assert e2.ride.history.total == 4
    e2.ride.close()


# ------------------------------------------------------------------------ LiveChart
def test_live_chart_draws_once_then_appends():
    h = History()
    _add(h, [1, 2, 3])
    chart = FakeChart()
    live = _live(chart, h, False)
    live.sync()
    assert chart.updates == 1 and not chart.scripts  # the whole history, once
    assert chart.options["series"][1]["data"] == [[1, 200], [2, 200], [3, 200]]

    _add(h, [4, 5])
    live.sync()
    assert chart.updates == 1  # no full update any more
    assert chart.sent()["power"] == [[4, 200], [5, 200]]  # only the new points
    assert chart.sent()["hr"] == [[4, 140], [5, 140]] and chart.sent()["axis"] is None
    assert len(chart.options["series"][1]["data"]) == 5  # the server copy stays complete

    live.sync()
    assert len(chart.scripts) == 1  # nothing new: nothing sent


def test_live_chart_redraws_when_the_history_restarts():
    h = History()
    _add(h, [1, 2, 3])
    chart = FakeChart()
    live = _live(chart, h, True)
    live.sync()
    h.restart()  # a new or resumed ride
    _add(h, [1])
    live.sync()
    assert chart.updates == 2 and chart.options["series"][1]["data"] == [[1, 200]]


def test_live_chart_free_ride_window_and_trim():
    h = History(maxlen=4)
    chart = FakeChart()
    live = _live(chart, h, True)
    live.sync()
    assert chart.options["xAxis"] == {"min": 0, "max": 600}
    _add(h, [1501, 1502, 1503])  # well into the ride
    live.sync()
    _add(h, [1504, 1505, 1506])
    live.sync()
    assert chart.sent()["axis"] == {"min": 306, "max": 1506}  # the last 20 minutes
    assert chart.sent()["max"] == 4
    assert [x for x, _ in chart.options["series"][1]["data"]] == [1503, 1504, 1505, 1506]


def test_power_hr_skips_missing_values():
    points = [HistoryPoint(1, None, None, None, None), HistoryPoint(2.4, 180.0, 0, 90, None)]
    assert widgets._power_hr(points) == ([[2, 180.0]], [])


NODE_HARNESS = """
const calls = [];
const el = {
  options: {series: [{data: [[0, 100]]}, {data: [[1, 1], [2, 2]]}, {data: []}], xAxis: {}},
  chart: {setOption: (o) => calls.push(o)},
};
globalThis.getElement = (id) => (id === 7 ? el : undefined);
%s;
console.log(JSON.stringify({options: el.options, calls}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js to run the script")
def test_append_script_in_javascript():
    h = History(maxlen=3)
    chart = FakeChart()
    live = _live(chart, h, True)
    live.sync()
    _add(h, [1, 2])
    live.sync()
    script = chart.scripts[-1]
    out = subprocess.run(
        ["node", "-e", NODE_HARNESS % script], capture_output=True, text=True, check=True
    )
    result = json.loads(out.stdout)
    series = result["options"]["series"]
    assert series[1]["data"] == [[2, 2], [1, 200], [2, 200]]  # appended, oldest trimmed
    assert series[2]["data"] == [[1, 140], [2, 140]]
    assert series[0]["data"] == [[0, 100]]  # the target is left alone
    assert result["options"]["xAxis"] == {"min": 0, "max": 600}
    [call] = result["calls"]
    assert call["series"][0] == {} and call["series"][1]["data"] == series[1]["data"]
    assert call["xAxis"] == {"min": 0, "max": 600}
