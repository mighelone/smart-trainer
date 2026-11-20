"""Cadence alert: warns when the rider stays off the step's cadence target for a while."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CADENCE_TOLERANCE_RPM = 10  # off target by more than this is a cadence alert...
CADENCE_WARN_AFTER_S = 5.0  # ...once it lasts this long (a short dip doesn't flash)


@dataclass
class CadenceCheck:
    """Raises a cadence alert after ``CADENCE_WARN_AFTER_S`` off target, and clears it.

    Attributes:
        alert (str | None): ``"low"`` or ``"high"`` while the rider has been more than
            ``CADENCE_TOLERANCE_RPM`` off the target for ``CADENCE_WARN_AFTER_S``;
            ``None`` otherwise.
    """

    alert: Literal["low", "high"] | None = None
    _off_s: float = field(default=0.0, repr=False)
    _step: int | None = field(default=None, repr=False)

    def reset(self) -> None:
        """Clear the alert and the time spent off target."""
        self.alert = None
        self._off_s = 0.0

    def update(
        self, target: int | None, cadence: float | None, step: int | None, paused: bool, dt: float
    ) -> None:
        """Raise or clear the alert for this tick.

        Time off target only counts within one step: a new step starts with a clean
        slate, so the rider has ``CADENCE_WARN_AFTER_S`` to adapt to its target.

        Args:
            target (int | None): Cadence target of the current step in rpm, if any.
            cadence (float | None): Current cadence in rpm.
            step (int | None): Index of the current workout step, if any.
            paused (bool): Whether the ride is paused (no alert then).
            dt (float): Seconds since the previous tick.
        """
        if step != self._step:
            self._step = step
            self.reset()
        if paused or target is None or cadence is None:
            self.reset()
            return
        off = cadence - target
        if abs(off) <= CADENCE_TOLERANCE_RPM:
            self.reset()
            return
        self._off_s += dt
        if self._off_s >= CADENCE_WARN_AFTER_S:
            self.alert = "low" if off < 0 else "high"
