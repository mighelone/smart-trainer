"""Workout profile chart and the live power / heart-rate chart."""

from __future__ import annotations

import json

from nicegui import ui

from smart_trainer.core.history import History, HistoryPoint
from smart_trainer.core.workout import Workout, WorkoutRunner


def workout_chart(workout: Workout | None, ftp: int, height: str = "h-64", live: bool = True):
    """Create an ECharts chart of the workout target, optionally with live series.

    Args:
        workout (Workout | None): Workout whose target profile is drawn.
        ftp (int): Rider's FTP in watts, used to convert targets to watts.
        height (str): Tailwind height class for the chart.
        live (bool): Include the live power and heart-rate series.

    Returns:
        ui.echart: The chart element.
    """
    total = workout.duration_s if workout else 600
    series = [
        {
            "type": "line",
            "name": "Target",
            "data": [],
            "step": False,
            "showSymbol": False,
            "lineStyle": {"width": 1, "color": "#4f8cff", "opacity": 0.8},
            "areaStyle": {"opacity": 0.28, "color": "#4f8cff"},
            "z": 1,
        },
        {
            "type": "line",
            "name": "Power",
            "data": [],
            "showSymbol": False,
            "lineStyle": {"width": 1.5, "color": "#facc15"},
            "sampling": "lttb",  # drawn downsampled in the browser, keeping the peaks
            "z": 3,
        },
        {
            "type": "line",
            "name": "HR",
            "data": [],
            "showSymbol": False,
            "yAxisIndex": 1,
            "lineStyle": {"width": 1.5, "color": "#f43f5e"},
            "sampling": "lttb",
            "z": 2,
        },
    ]
    if workout:
        pts = WorkoutRunner(workout, ftp).profile()
        series[0]["data"] = [[t, round(w)] for t, w in pts]
    muted = "#8a96a3"
    axis_label = {"color": muted, "fontSize": 11}
    opts = {
        "animation": False,
        "backgroundColor": "transparent",
        "textStyle": {"color": muted},
        "grid": {"left": 42, "right": 42, "top": 22, "bottom": 24},
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": "#19212b",
            "borderColor": "rgba(255,255,255,0.08)",
            "textStyle": {"color": "#e8edf2"},
        },
        "xAxis": {
            "type": "value",
            "min": 0,
            "max": total,
            "axisLabel": {**axis_label, ":formatter": 'v => Math.floor(v / 60) + "\'"'},
            "axisLine": {"lineStyle": {"color": "rgba(255,255,255,0.12)"}},
            "axisTick": {"show": False},
            "splitLine": {"show": False},
        },
        "yAxis": [
            {
                "type": "value",
                "name": "W",
                "min": 0,
                "nameTextStyle": axis_label,
                "axisLabel": axis_label,
                "splitLine": {"lineStyle": {"color": "rgba(255,255,255,0.06)"}},
            },
            {
                "type": "value",
                "name": "bpm",
                "min": 60,
                "max": 200,
                "nameTextStyle": axis_label,
                "axisLabel": axis_label,
                "splitLine": {"show": False},
            },
        ],
        "series": series if live else series[:1],
    }
    if not live:  # compact preview: power axis only, no gridlines
        opts["yAxis"] = opts["yAxis"][:1]
        opts["yAxis"][0].update(name="", splitNumber=2, splitLine={"show": False})
        opts["grid"].update(top=8, right=12)
    chart = ui.echart(opts).classes(f"w-full {height}")
    return chart


FREE_RIDE_WINDOW_S = 1200  # a free ride shows the last 20 minutes
FREE_RIDE_MIN_AXIS_S = 600

# Appends the new points to the chart's own copy of its options (so a later redraw from
# them is complete), trims them like the ride history, and redraws only the live series.
_APPEND_JS = """
(() => {
  const el = getElement(%(id)d);
  if (!el || !el.options) return;
  const d = %(data)s;
  const series = el.options.series;
  for (const [i, pts] of [[1, d.power], [2, d.hr]]) {
    const data = series[i].data;
    data.push(...pts);
    if (data.length > d.max) data.splice(0, data.length - d.max);
  }
  if (d.axis) Object.assign(el.options.xAxis, d.axis);
  if (!el.chart) return;  // not drawn yet: it will draw from el.options when mounted
  el.chart.setOption({
    series: [{}, {data: series[1].data}, {data: series[2].data}],
    ...(d.axis ? {xAxis: d.axis} : {}),
  });
})()
"""


def _power_hr(points: list[HistoryPoint]) -> tuple[list[list[float]], list[list[float]]]:
    """Split history points into the power and heart-rate series.

    Args:
        points (list[HistoryPoint]): Points, oldest first.

    Returns:
        tuple[list[list[float]], list[list[float]]]: ``[x, watts]`` and ``[x, bpm]`` pairs,
        leaving out missing values.
    """
    power: list[list[float]] = [[round(p.x), p.power] for p in points if p.power is not None]
    hr: list[list[float]] = [[round(p.x), p.hr] for p in points if p.hr]
    return power, hr


class LiveChart:
    """Keeps the live power and heart-rate series of a workout chart in sync with a history.

    The full history is sent once (when the chart is created or the history restarts);
    after that each :meth:`sync` sends only the new points, so the traffic per refresh
    doesn't grow with the length of the ride.

    Attributes:
        chart (ui.echart): A chart made by :func:`workout_chart` with ``live=True``.
        history (History): The ride history drawn.
        free_ride (bool): Whether the chart follows a free ride (sliding time window).
    """

    def __init__(self, chart: ui.echart, history: History, free_ride: bool) -> None:
        """Wrap a chart.

        Args:
            chart (ui.echart): The chart to keep in sync.
            history (History): The ride history to draw.
            free_ride (bool): Whether the chart follows a free ride.
        """
        self.chart = chart
        self.history = history
        self.free_ride = free_ride
        self._gen: int | None = None
        self._seen = 0

    def sync(self) -> None:
        """Bring the chart up to date with the history."""
        h = self.history
        new = None if self._gen is None else h.since(self._gen, self._seen)
        self._gen, self._seen = h.gen, h.total
        if new is None:
            self._redraw(list(h))
        elif new:
            self._append(new)

    def _axis(self, last: HistoryPoint | None) -> dict | None:
        """The x-axis range of a free ride: the last ``FREE_RIDE_WINDOW_S`` seconds.

        Args:
            last (HistoryPoint | None): The latest point.

        Returns:
            dict | None: ``{"min", "max"}``, or ``None`` when following a workout.
        """
        if not self.free_ride:
            return None
        end = max(FREE_RIDE_MIN_AXIS_S, last.x if last else 0)
        return {"min": max(0, end - FREE_RIDE_WINDOW_S), "max": end}

    def _redraw(self, points: list[HistoryPoint]) -> None:
        """Send the whole history.

        Args:
            points (list[HistoryPoint]): Every point to draw, oldest first.
        """
        opts = self.chart.options
        opts["series"][1]["data"], opts["series"][2]["data"] = _power_hr(points)
        if axis := self._axis(points[-1] if points else None):
            opts["xAxis"].update(axis)
            opts["series"][0]["data"] = []
        self.chart.update()

    def _append(self, points: list[HistoryPoint]) -> None:
        """Send only the new points (the server keeps its copy of the options in step).

        Args:
            points (list[HistoryPoint]): The new points, oldest first.
        """
        power, hr = _power_hr(points)
        opts = self.chart.options
        axis = self._axis(points[-1])
        # The options are observed: without suspending, every change below would send
        # the whole chart to the browser again, which is what this method avoids.
        with self.chart.props.suspend_updates():
            for i, pts in ((1, power), (2, hr)):
                data = opts["series"][i]["data"]
                data += pts
                del data[: max(0, len(data) - self.history.maxlen)]
            if axis:
                opts["xAxis"].update(axis)
        payload = {"power": power, "hr": hr, "max": self.history.maxlen, "axis": axis}
        self.chart.client.run_javascript(
            _APPEND_JS % {"id": self.chart.id, "data": json.dumps(payload)}
        )
