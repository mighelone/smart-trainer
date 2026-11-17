"""Reusable UI pieces."""

from __future__ import annotations

from nicegui import ui

from smart_trainer.core.workout import Workout, WorkoutRunner


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
    """A big metric tile: label, value, unit, optional sub-line.

    Attributes:
        value (ui.label): Label showing the main value.
        sub (ui.label): Label showing the secondary line.
    """

    def __init__(self, label: str, unit: str = "", big: bool = False, color: str = "") -> None:
        """Create the tile in the current UI context.

        Args:
            label (str): Metric name shown above the value.
            unit (str): Unit shown next to the value.
            big (bool): Use a larger font for the value.
            color (str): Tailwind classes applied to the value (e.g. a text color).
        """
        size = "text-7xl" if big else "text-5xl"
        with ui.card().classes("items-center min-w-[150px] grow p-3 gap-0"):
            ui.label(label).classes("text-xs uppercase tracking-widest opacity-60")
            with ui.row().classes("items-baseline gap-1"):
                self.value = ui.label("--").classes(f"{size} font-bold tabular-nums {color}")
                ui.label(unit).classes("text-lg opacity-60")
            self.sub = ui.label("").classes("text-sm opacity-70 tabular-nums")

    def set(self, value: str, sub: str = "") -> None:
        """Update the displayed value and sub-line.

        Args:
            value (str): Main value text.
            sub (str): Secondary line text.
        """
        self.value.set_text(value)
        self.sub.set_text(sub)


def zone_color(watts: float | None, ftp: int) -> str:
    """Color for a power value according to its Coggan power zone.

    Args:
        watts (float | None): Power in watts.
        ftp (int): Rider's FTP in watts.

    Returns:
        str: Hex color string (grey if ``watts`` is ``None``).
    """
    if watts is None:
        return "#64748b"
    r = watts / ftp
    for limit, color in (
        (0.55, "#94a3b8"),
        (0.75, "#3b82f6"),
        (0.90, "#22c55e"),
        (1.05, "#eab308"),
        (1.20, "#f97316"),
        (9e9, "#ef4444"),
    ):
        if r < limit:
            return color
    return "#ef4444"


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
            "lineStyle": {"width": 0},
            "areaStyle": {"opacity": 0.45, "color": "#3b82f6"},
            "z": 1,
        },
        {
            "type": "line",
            "name": "Power",
            "data": [],
            "showSymbol": False,
            "lineStyle": {"width": 1.5, "color": "#facc15"},
            "z": 3,
        },
        {
            "type": "line",
            "name": "HR",
            "data": [],
            "showSymbol": False,
            "yAxisIndex": 1,
            "lineStyle": {"width": 1.5, "color": "#f43f5e"},
            "z": 2,
        },
    ]
    if workout:
        pts = WorkoutRunner(workout, ftp).profile()
        series[0]["data"] = [[t, round(w)] for t, w in pts]
    opts = {
        "animation": False,
        "grid": {"left": 45, "right": 45, "top": 15, "bottom": 25},
        "tooltip": {"trigger": "axis"},
        "xAxis": {
            "type": "value",
            "min": 0,
            "max": total,
            "axisLabel": {":formatter": 'v => Math.floor(v / 60) + "\'"'},
            "splitLine": {"show": False},
        },
        "yAxis": [
            {"type": "value", "name": "W", "min": 0, "splitLine": {"lineStyle": {"opacity": 0.15}}},
            {"type": "value", "name": "bpm", "min": 60, "max": 200, "splitLine": {"show": False}},
        ],
        "series": series if live else series[:1],
    }
    chart = ui.echart(opts).classes(f"w-full {height}")
    return chart
