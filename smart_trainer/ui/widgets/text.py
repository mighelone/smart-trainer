"""Text formatting for the pages: numbers, durations and workout steps."""

from __future__ import annotations

from smart_trainer.core.workout import Step


def fmt_time(seconds: float | None) -> str:
    """Format a duration as ``MM:SS`` or ``H:MM:SS``.

    Args:
        seconds (float | None): Duration in seconds.

    Returns:
        str: The formatted duration, or ``"--:--"`` if ``None``.
    """
    if seconds is None:
        return "--:--"
    hours, rem = divmod(int(max(0, seconds)), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def fmt(value, digits: int = 0, unit: str = "") -> str:
    """Format a number with a fixed number of decimals and an optional unit.

    Args:
        value (float | int | None): Value to format.
        digits (int): Number of decimal places.
        unit (str): Unit suffix appended to the value.

    Returns:
        str: The formatted value, or ``"--"`` if ``None``.
    """
    if value is None:
        return "--"
    return f"{value:.{digits}f}{unit}"


def next_step_text(step: Step, ftp: int, intensity: float = 1.0) -> str:
    """Describe a step for the "next" line and the cue: cue text, target and duration.

    Args:
        step (Step): The step.
        ftp (int): Rider's FTP in watts.
        intensity (float): Workout intensity multiplier.

    Returns:
        str: E.g. ``"Sprint · 400 W for 00:30 @ 110 rpm"`` or ``"Free ride for 05:00"``.
    """
    prefix = f"{step.text} · " if step.text else ""
    target = step.target_w(0, ftp)
    what = "Free ride" if target is None else f"{fmt(target * intensity)} W"
    cadence = f" @ {step.cadence} rpm" if step.cadence else ""
    return f"{prefix}{what} for {fmt_time(step.duration_s)}{cadence}"


def step_title(index: int, step: Step) -> str:
    """Title of the current step: its cue text (or a generic name) and cadence target.

    Args:
        index (int): Index of the step in the workout.
        step (Step): The step.

    Returns:
        str: E.g. ``"High Cadence · 100 rpm"`` or ``"Interval 3"``.
    """
    title = step.text or ("Free ride" if step.free_ride else f"Interval {index + 1}")
    return f"{title} · {step.cadence} rpm" if step.cadence else title
