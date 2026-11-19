"""The ride's chart history: the recent points, and what a viewer hasn't seen yet."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field

HISTORY_MAXLEN = 3 * 3600  # chart points kept: 3 h at 1 Hz


@dataclass
class HistoryPoint:
    """One point of the live ride chart.

    Attributes:
        x (float): Position on the chart's time axis: workout elapsed seconds, or
            active ride seconds in a free ride (what the session records as ``elapsed``).
        power (float | None): Power in watts.
        hr (int | None): Heart rate in bpm.
        cadence (float | None): Cadence in rpm.
        target (float | None): Workout target power in watts.
    """

    x: float
    power: float | None
    hr: int | None
    cadence: float | None
    target: float | None


@dataclass(eq=False)
class History:
    """The last ``maxlen`` chart points of a ride, with counters for incremental viewers.

    A viewer (e.g. a chart) remembers :attr:`gen` and :attr:`total` when it syncs, and
    later asks :meth:`since` for only the points it lacks.

    Attributes:
        maxlen (int): Number of points kept; older ones are dropped.
        gen (int): Changes whenever the history is restarted (new or resumed ride), so
            a viewer knows to redraw instead of appending.
        total (int): Points added since the last restart, including those dropped.
    """

    maxlen: int = HISTORY_MAXLEN
    gen: int = field(default=0, init=False)
    total: int = field(default=0, init=False)
    _points: deque[HistoryPoint] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Create the bounded point buffer."""
        self._points = deque(maxlen=self.maxlen)

    def __iter__(self) -> Iterator[HistoryPoint]:
        """Iterate over the kept points, oldest first.

        Returns:
            Iterator[HistoryPoint]: The points.
        """
        return iter(self._points)

    def __len__(self) -> int:
        """Number of kept points.

        Returns:
            int: At most :attr:`maxlen`.
        """
        return len(self._points)

    def restart(self) -> None:
        """Drop every point and start a new generation."""
        self._points.clear()
        self.gen += 1
        self.total = 0

    def add(self, point: HistoryPoint) -> None:
        """Append a point (dropping the oldest when full).

        Args:
            point (HistoryPoint): The new point.
        """
        self._points.append(point)
        self.total += 1

    def since(self, gen: int, seen: int) -> list[HistoryPoint] | None:
        """The points a viewer hasn't got yet.

        Args:
            gen (int): The :attr:`gen` the viewer last synced with.
            seen (int): The :attr:`total` the viewer last synced with.

        Returns:
            list[HistoryPoint] | None: The new points (oldest first, maybe empty), or
            ``None`` when the viewer must redraw from the whole history (it was
            restarted, or more points arrived than are still kept).
        """
        new = self.total - seen
        if gen != self.gen or new < 0 or new > len(self._points):
            return None
        return [self._points[-i] for i in range(new, 0, -1)]  # O(1) access at the ends
