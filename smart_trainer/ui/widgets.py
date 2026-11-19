"""Reusable UI pieces."""

from __future__ import annotations

import json
from collections.abc import Callable

from nicegui import Client, ui

from smart_trainer.config import DEFAULT_POWER_ZONES
from smart_trainer.core.history import History, HistoryPoint
from smart_trainer.core.ride import Ride
from smart_trainer.core.workout import Step, Workout, WorkoutRunner


def fmt_time(seconds: float | None) -> str:
    """Format a duration as ``MM:SS`` or ``H:MM:SS``.

    Args:
        seconds (float | None): Duration in seconds.

    Returns:
        str: The formatted duration, or ``"--:--"`` if ``None``.
    """
    if seconds is None:
        return "--:--"
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt(v, digits: int = 0, unit: str = "") -> str:
    """Format a number with a fixed number of decimals and an optional unit.

    Args:
        v (float | int | None): Value to format.
        digits (int): Number of decimal places.
        unit (str): Unit suffix appended to the value.

    Returns:
        str: The formatted value, or ``"--"`` if ``None``.
    """
    if v is None:
        return "--"
    return f"{v:.{digits}f}{unit}"


class Tile:
    """A metric tile with a uniform layout: label, value, unit, zone badge and sub-line.

    Every tile has the same structure (and the sub-line always reserves its space), so
    tiles in a grid line up regardless of what they currently show. The zone is shown
    as a colored accent bar and a ``Zn`` badge, keeping the value itself high-contrast.

    Attributes:
        card (ui.element): The tile container.
        value (ui.label): Label showing the main value.
        sub (ui.label): Label showing the secondary line.
        badge (ui.label): Zone badge, hidden when no zone is known.
    """

    def __init__(
        self,
        label: str,
        unit: str = "",
        icon: str | None = None,
        size: str = "md",
        accent: str | None = None,
    ) -> None:
        """Create the tile in the current UI context.

        Args:
            label (str): Metric name shown above the value.
            unit (str): Unit shown next to the value.
            icon (str | None): Material icon shown before the label.
            size (str): ``"lg"`` for primary metrics, ``"md"`` for secondary ones.
            accent (str | None): Default accent color, used when no zone is set.
        """
        self._default = accent
        self._accent: str | None = None
        self._badge: str | None = None
        with ui.element("div").classes(f"metric metric-{size}") as self.card:
            with ui.element("div").classes("metric-head"):
                with ui.element("div").classes("metric-label"):
                    if icon:
                        ui.icon(icon).classes("metric-icon")
                    ui.label(label)
                self.badge = ui.label("").classes("metric-badge")
                self.badge.set_visibility(False)
            with ui.element("div").classes("metric-body"):
                self.value = ui.label("--").classes("metric-value")
                if unit:
                    ui.label(unit).classes("metric-unit")
            self.sub = ui.label("").classes("metric-sub")
        self.set_zone(None)

    def set(self, value: str, sub: str = "") -> None:
        """Update the displayed value and sub-line.

        Args:
            value (str): Main value text.
            sub (str): Secondary line text.
        """
        self.value.set_text(value)
        self.sub.set_text(sub)

    def set_zone(self, color: str | None, badge: str | None = None) -> None:
        """Show a zone: accent color and badge text (``None`` restores the default).

        Args:
            color (str | None): CSS color of the zone.
            badge (str | None): Badge text, e.g. ``"Z3"``; hidden if ``None``.
        """
        accent = color or self._default or "var(--faint)"
        if accent != self._accent:
            self._accent = accent
            self.card.style(f"--accent: {accent}")
        if badge != self._badge:
            self._badge = badge
            self.badge.set_text(badge or "")
            self.badge.set_visibility(badge is not None)


ZONE_COLORS = ("#94a3b8", "#3b82f6", "#22c55e", "#eab308", "#f97316", "#ef4444", "#a855f7")
NO_ZONE_COLOR = "#64748b"


def zone_index(value: float, bounds: list[int] | tuple[int, ...]) -> int:
    """0-based zone of a value, given ascending zone upper bounds.

    Args:
        value (float): The value (same unit as ``bounds``).
        bounds (list[int] | tuple[int, ...]): Zone upper bounds; the last zone is open-ended.

    Returns:
        int: Zone index, at most ``len(bounds) - 1``.
    """
    for i, upper in enumerate(bounds):
        if value < upper:
            return i
    return len(bounds) - 1


def _color(i: int) -> str:
    """Color of the ``i``-th zone (the last color repeats past seven zones)."""
    return ZONE_COLORS[min(i, len(ZONE_COLORS) - 1)]


def zone_color(watts: float | None, ftp: int, zones: list[int] | None = None) -> str:
    """Color for a power value according to the rider's power zone.

    Args:
        watts (float | None): Power in watts.
        ftp (int): Rider's FTP in watts.
        zones (list[int] | None): Zone upper bounds as % of FTP; ``None`` uses Coggan zones.

    Returns:
        str: Hex color string (grey if ``watts`` is ``None``).
    """
    if watts is None or ftp <= 0:
        return NO_ZONE_COLOR
    return _color(zone_index(watts / ftp * 100, zones or DEFAULT_POWER_ZONES))


def hr_zone_color(bpm: float | None, hr_zones: list[int]) -> str | None:
    """Color for a heart rate according to the rider's HR zones.

    Args:
        bpm (float | None): Heart rate in bpm.
        hr_zones (list[int]): HR zone upper bounds in bpm.

    Returns:
        str | None: Hex color string, or ``None`` if HR or zones are unknown.
    """
    if bpm is None or not hr_zones:
        return None
    return _color(zone_index(bpm, hr_zones))


def power_zone(
    watts: float | None, ftp: int, zones: list[int] | None = None
) -> tuple[str | None, str | None]:
    """Zone color and badge text (e.g. ``"Z3"``) for a power value.

    Args:
        watts (float | None): Power in watts.
        ftp (int): Rider's FTP in watts.
        zones (list[int] | None): Zone upper bounds as % of FTP; ``None`` uses Coggan zones.

    Returns:
        tuple[str | None, str | None]: ``(color, badge)``, or ``(None, None)`` if unknown.
    """
    if watts is None or ftp <= 0:
        return None, None
    i = zone_index(watts / ftp * 100, zones or DEFAULT_POWER_ZONES)
    return _color(i), f"Z{i + 1}"


def hr_zone(bpm: float | None, hr_zones: list[int]) -> tuple[str | None, str | None]:
    """Zone color and badge text (e.g. ``"Z2"``) for a heart rate.

    Args:
        bpm (float | None): Heart rate in bpm.
        hr_zones (list[int]): HR zone upper bounds in bpm.

    Returns:
        tuple[str | None, str | None]: ``(color, badge)``, or ``(None, None)`` if unknown.
    """
    if bpm is None or not hr_zones:
        return None, None
    i = zone_index(bpm, hr_zones)
    return _color(i), f"Z{i + 1}"


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


# Defines window.smartTrainerBeep(count) once per page: short beeps made with the Web Audio
# API (no sound file). Browsers only allow audio after a user gesture, so the first click or
# key press on the page unlocks it (pressing Start does).
BEEP_JS = """
window.smartTrainerBeep ??= (() => {
  let ctx = null;
  const audio = () => (ctx ??= new (window.AudioContext || window.webkitAudioContext)());
  const unlock = () => { if (audio().state === "suspended") audio().resume(); };
  for (const ev of ["pointerdown", "keydown"]) {
    document.addEventListener(ev, unlock, {capture: true});
  }
  return (count = 2) => {
    const c = audio();
    if (c.state === "suspended") c.resume();
    const t0 = c.currentTime + 0.05;
    for (let i = 0; i < count; i++) {
      const osc = c.createOscillator(), gain = c.createGain();
      const t = t0 + i * 0.25;
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.3, t + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.15);
      osc.connect(gain).connect(c.destination);
      osc.start(t);
      osc.stop(t + 0.16);
    }
  };
})();
"""


def next_step_text(step: Step, ftp: int, intensity: float = 1.0) -> str:
    """Describe a step for the "next" line and the cue: cue text, target and duration.

    Args:
        step (Step): The step.
        ftp (int): Rider's FTP in watts.
        intensity (float): Workout intensity multiplier.

    Returns:
        str: E.g. ``"Sprint · 400 W for 00:30"`` or ``"Free ride for 05:00"``.
    """
    prefix = f"{step.text} · " if step.text else ""
    target = step.target_w(0, ftp)
    what = "Free ride" if target is None else f"{fmt(target * intensity)} W"
    return f"{prefix}{what} for {fmt_time(step.duration_s)}"


class CuePlayer:
    """Plays a beep and shows the next step when the ride announces a step change.

    Create it while building a page: cues raised before the page opened aren't played.

    Attributes:
        BEEPS (int): Beeps per cue.
    """

    BEEPS = 2

    def __init__(self, ride: Ride, enabled: Callable[[], bool]) -> None:
        """Install the beep in the page and remember the cues already raised.

        Args:
            ride (Ride): The ride whose cues are played.
            enabled (Callable[[], bool]): Whether cues are on (read at each cue, so
                the Settings switch applies right away).
        """
        self.ride = ride
        self.enabled = enabled
        self.client = ui.context.client
        self.seen = ride.cue.seq if ride.cue else 0
        self.install()

    @staticmethod
    def install() -> None:
        """Define the beep in the page being built."""
        ui.add_head_html(f"<script>{BEEP_JS}</script>")

    def check(self) -> None:
        """Play the latest cue if it's new (and cues are on)."""
        cue, runner = self.ride.cue, self.ride.runner
        if cue is None or cue.seq <= self.seen:
            return
        self.seen = cue.seq
        if not self.enabled() or runner is None:
            return
        self.beep(self.client, self.BEEPS)
        text = next_step_text(cue.next_step, runner.ftp, runner.intensity)
        ui.notify(f"In {cue.in_s:.0f} s · {text}", position="top", timeout=cue.in_s * 1000)

    @staticmethod
    def beep(client: Client, count: int = 2) -> None:
        """Play the beeps in a page.

        Args:
            client (Client): The page's client.
            count (int): Number of beeps.
        """
        client.run_javascript(f"window.smartTrainerBeep?.({count})")
