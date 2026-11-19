"""Training metrics over 1 Hz power samples: normalized power, intensity, TSS and work.

The samples are one per recorded second (paused seconds left out), as the session
records them; missing readings (``None``) are skipped.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

NP_WINDOW_S = 30  # normalized power: rolling average over 30 s


def _powers(samples: Iterable[float | None]) -> list[float]:
    """Drop missing readings.

    Args:
        samples (Iterable[float | None]): Power in watts, one per second.

    Returns:
        list[float]: The readings that exist.
    """
    return [p for p in samples if p is not None]


def normalized_power(samples: Iterable[float | None]) -> float | None:
    """Normalized power: 4th root of the mean 4th power of the 30 s rolling average.

    Args:
        samples (Iterable[float | None]): Power in watts, one per second.

    Returns:
        float | None: NP in watts, or ``None`` with fewer than 30 readings.
    """
    powers = _powers(samples)
    if len(powers) < NP_WINDOW_S:
        return None
    window: deque[float] = deque(maxlen=NP_WINDOW_S)
    total = fourth = 0.0
    count = 0
    for p in powers:
        if len(window) == NP_WINDOW_S:
            total -= window[0]
        window.append(p)
        total += p
        if len(window) == NP_WINDOW_S:
            fourth += (total / NP_WINDOW_S) ** 4
            count += 1
    return (fourth / count) ** 0.25


def work_j(samples: Iterable[float | None]) -> float:
    """Mechanical work: each reading counts for one second.

    Args:
        samples (Iterable[float | None]): Power in watts, one per second.

    Returns:
        float: Work in joules.
    """
    return sum(_powers(samples))


def intensity_factor(np_w: float, ftp: int) -> float:
    """Intensity factor: normalized power relative to FTP.

    Args:
        np_w (float): Normalized power in watts.
        ftp (int): Functional threshold power in watts (> 0).

    Returns:
        float: IF (1.0 = riding at FTP).
    """
    return np_w / ftp


def training_stress_score(np_w: float, ftp: int, seconds: float) -> float:
    """Training stress score: 100 for one hour at FTP.

    Args:
        np_w (float): Normalized power in watts.
        ftp (int): Functional threshold power in watts (> 0).
        seconds (float): Riding (timer) time in seconds.

    Returns:
        float: TSS.
    """
    return seconds / 3600 * intensity_factor(np_w, ftp) ** 2 * 100
