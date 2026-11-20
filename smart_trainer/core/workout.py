"""Workout model, parsers (intervals.icu workout_doc, Zwift .zwo) and runner."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field

# Mid-point of Coggan power zones (fraction of FTP) for zone-based targets
ZONE_MID = {1: 0.50, 2: 0.65, 3: 0.82, 4: 0.97, 5: 1.12, 6: 1.35, 7: 1.60}


@dataclass
class Step:
    """A single workout step with a steady or ramped power target.

    Attributes:
        duration_s (int): Duration of the step in seconds.
        start (float): Target at the start of the step, as a fraction of FTP
            (``unit="ftp"``) or in watts (``unit="watts"``).
        end (float | None): Target at the end of the step for ramps; ``None``
            for a steady step.
        unit (str): Unit of ``start``/``end``, either ``"ftp"`` or ``"watts"``.
        text (str): Optional cue text shown during the step.
        free_ride (bool): If ``True`` the step has no power target.
        cadence (int | None): Optional target cadence in rpm.
    """

    duration_s: int
    start: float  # fraction of FTP (unit="ftp") or watts (unit="watts")
    end: float | None = None  # ramp end; None = steady
    unit: str = "ftp"
    text: str = ""
    free_ride: bool = False
    cadence: int | None = None

    def target_w(self, frac: float, ftp: int) -> float | None:
        """Compute the power target at a given point of the step.

        Args:
            frac (float): Progress through the step, from 0.0 (start) to 1.0 (end).
            ftp (int): Rider's functional threshold power in watts.

        Returns:
            float | None: Target power in watts, or ``None`` for a free-ride step.
        """
        if self.free_ride:
            return None
        end = self.start if self.end is None else self.end
        target = self.start + (end - self.start) * frac
        return target * ftp if self.unit == "ftp" else target


@dataclass
class Workout:
    """A structured workout made of a sequence of steps.

    Attributes:
        name (str): Workout name.
        steps (list[Step]): Ordered list of workout steps.
        description (str): Free-text description of the workout.
        source_id (str | None): Identifier in the originating system
            (e.g. intervals.icu event id), if any.
    """

    name: str
    steps: list[Step] = field(default_factory=list)
    description: str = ""
    source_id: str | None = None  # e.g. intervals.icu event id

    @property
    def duration_s(self) -> int:
        """Total duration of the workout.

        Returns:
            int: Sum of all step durations in seconds.
        """
        return sum(s.duration_s for s in self.steps)

    def to_dict(self) -> dict:
        """Serialize the workout to a plain dictionary.

        Returns:
            dict: Dictionary representation of the workout and its steps.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Workout:
        """Build a workout from a dictionary produced by :meth:`to_dict`.

        Args:
            data (dict): Dictionary with ``name`` and optional ``steps``,
                ``description`` and ``source_id`` keys.

        Returns:
            Workout: The reconstructed workout.
        """
        return cls(
            name=data["name"],
            steps=[Step(**s) for s in data.get("steps", [])],
            description=data.get("description", ""),
            source_id=data.get("source_id"),
        )

    def step_starts(self) -> list[int]:
        """Compute the start time of each step.

        Returns:
            list[int]: Start offset of each step in seconds from workout start.
        """
        out, start = [], 0
        for step in self.steps:
            out.append(start)
            start += step.duration_s
        return out


# --------------------------------------------------------------- intervals.icu
def zone_mid(zone: int, zones: list[int] | None = None) -> float:
    """Representative target for a power zone, as a fraction of FTP.

    Args:
        zone (int): 1-based zone number.
        zones (list[int] | None): Zone upper bounds as % of FTP (the last zone is
            open-ended). ``None`` uses the default Coggan midpoints.

    Returns:
        float: Target as a fraction of FTP.
    """
    if not zones:
        return ZONE_MID.get(zone, 0.6)
    index = min(max(zone, 1), len(zones)) - 1
    lower = zones[index - 1] / 100.0 if index > 0 else None
    upper = zones[index] / 100.0
    if lower is None:  # Z1 starts at 0: aim near its top, not at 0.5 * upper
        return 0.9 * upper
    if index == len(zones) - 1:  # open-ended top zone
        return lower + 0.10
    return (lower + upper) / 2


def _icu_power(
    power: dict | None, ftp: int, zones: list[int] | None = None
) -> tuple[float, float | None, str] | None:
    """Convert an intervals.icu power target into step target values.

    Args:
        power (dict | None): intervals.icu power target (``units``, ``value`` or
            ``start``/``end``).
        ftp (int): Rider's functional threshold power in watts.
        zones (list[int] | None): Rider's power zone upper bounds (% of FTP).

    Returns:
        tuple[float, float | None, str] | None: ``(start, end, unit)`` tuple, or
        ``None`` if the target is missing or uses unsupported units.
    """
    if not power:
        return None
    units = (power.get("units") or "%ftp").lower()
    start = power.get("start", power.get("value"))
    end = power.get("end") if "start" in power else None
    if start is None:
        return None
    if units in ("%ftp", "percent_ftp"):
        return start / 100.0, (end / 100.0 if end is not None else None), "ftp"
    if units in ("w", "watts"):
        return float(start), (float(end) if end is not None else None), "watts"
    if units in ("power_zone", "zone"):
        start_mid = zone_mid(int(round(start)), zones)
        end_mid = zone_mid(int(round(end)), zones) if end is not None else None
        return start_mid, end_mid, "ftp"
    return None


def parse_intervals_doc(
    doc: dict,
    name: str = "Workout",
    ftp: int = 250,
    source_id: str | None = None,
    zones: list[int] | None = None,
) -> Workout:
    """Parse an intervals.icu ``workout_doc`` into a workout.

    Repeat blocks are expanded, range targets on non-ramp steps are collapsed
    to their midpoint and steps without a power target become free-ride steps.

    Args:
        doc (dict): intervals.icu ``workout_doc`` structure.
        name (str): Name to give the workout.
        ftp (int): Rider's functional threshold power in watts.
        source_id (str | None): intervals.icu event id, if any.
        zones (list[int] | None): Rider's power zone upper bounds (% of FTP), used
            for zone targets; ``None`` uses the default Coggan zones.

    Returns:
        Workout: The parsed workout.
    """
    steps: list[Step] = []

    def walk(items: list[dict]) -> None:
        """Recursively flatten intervals.icu steps into ``steps``.

        Args:
            items (list[dict]): intervals.icu step dictionaries.
        """
        for it in items:
            if it.get("reps") and it.get("steps"):
                for _ in range(int(it["reps"])):
                    walk(it["steps"])
                continue
            if it.get("steps"):
                walk(it["steps"])
                continue
            dur = int(it.get("duration") or 0)
            if dur <= 0:
                continue
            pw = _icu_power(it.get("power"), ftp, zones)
            cad = it.get("cadence", {}) or {}
            cadence = cad.get("value") or cad.get("start")
            text = it.get("text") or ""
            if it.get("freeride") or pw is None:
                steps.append(Step(dur, 0.0, free_ride=True, text=text))
            else:
                start, end, unit = pw
                if not it.get("ramp") and end is not None:
                    start, end = (start + end) / 2, None  # range target -> midpoint
                steps.append(
                    Step(
                        dur, start, end, unit, text=text, cadence=int(cadence) if cadence else None
                    )
                )

    walk(doc.get("steps", []))
    return Workout(
        name=name, steps=steps, description=doc.get("description", "") or "", source_id=source_id
    )


# ------------------------------------------------------------------ Zwift .zwo
def _number(el: ET.Element, key: str, default: float = 0.0) -> float:
    """Read a numeric attribute of a .zwo step element.

    Args:
        el (ET.Element): The step element.
        key (str): Attribute name.
        default (float): Value when the attribute is missing.

    Returns:
        float: The attribute's value.
    """
    return float(el.attrib.get(key, default))


def parse_zwo(xml_text: str) -> Workout:
    """Parse a Zwift ``.zwo`` workout file.

    Args:
        xml_text (str): XML content of the ``.zwo`` file.

    Raises:
        ValueError: If the document has no ``<workout>`` element.

    Returns:
        Workout: The parsed workout.
    """
    root = ET.fromstring(xml_text)
    name = (root.findtext("name") or "ZWO workout").strip()
    desc = (root.findtext("description") or "").strip()
    steps: list[Step] = []
    container = root.find("workout")
    if container is None:
        raise ValueError("no <workout> element")
    for el in container:
        tag = el.tag.lower()
        cad = int(_number(el, "Cadence")) if "Cadence" in el.attrib else None
        if tag == "steadystate":
            steps.append(Step(int(_number(el, "Duration")), _number(el, "Power"), cadence=cad))
        elif tag in ("warmup", "cooldown", "ramp"):
            steps.append(
                Step(
                    int(_number(el, "Duration")),
                    _number(el, "PowerLow"),
                    _number(el, "PowerHigh"),
                    cadence=cad,
                )
            )
        elif tag == "intervalst":
            for _ in range(int(_number(el, "Repeat", 1))):
                steps.append(
                    Step(int(_number(el, "OnDuration")), _number(el, "OnPower"), cadence=cad)
                )
                steps.append(Step(int(_number(el, "OffDuration")), _number(el, "OffPower")))
        elif tag in ("freeride", "maxeffort"):
            steps.append(Step(int(_number(el, "Duration")), 0.0, free_ride=True))
    return Workout(name=name, steps=steps, description=desc)


# --------------------------------------------------------------------- runner
class WorkoutRunner:
    """Tracks workout progress in *active* seconds (paused time doesn't count).

    Attributes:
        workout (Workout): The workout being executed.
        ftp (int): Rider's functional threshold power in watts.
        elapsed (float): Active seconds elapsed since workout start.
        intensity (float): Multiplier applied to every power target.
        paused (bool): Whether progress is currently paused.
    """

    def __init__(self, workout: Workout, ftp: int, elapsed_s: float = 0.0) -> None:
        """Initialize the runner.

        Args:
            workout (Workout): The workout to execute.
            ftp (int): Rider's functional threshold power in watts.
            elapsed_s (float): Initial elapsed time in seconds, e.g. to resume
                a workout.
        """
        self.workout = workout
        self.ftp = ftp
        self.elapsed = float(elapsed_s)
        self.intensity = 1.0
        self.paused = False
        self._starts = workout.step_starts()

    @property
    def finished(self) -> bool:
        """Whether the workout has been completed.

        Returns:
            bool: ``True`` if elapsed time has reached the workout duration.
        """
        return self.elapsed >= self.workout.duration_s

    def advance(self, dt: float) -> None:
        """Advance the workout clock unless paused or finished.

        Args:
            dt (float): Time step in seconds.
        """
        if not self.paused and not self.finished:
            self.elapsed = min(self.elapsed + dt, float(self.workout.duration_s))

    def step_index(self) -> int:
        """Find the index of the step at the current elapsed time.

        Returns:
            int: Index of the current step (0 if the workout has no steps).
        """
        for index in range(len(self._starts) - 1, -1, -1):
            if self.elapsed >= self._starts[index]:
                return index
        return 0

    def current(self) -> tuple[int, Step, float, float]:
        """Describe the current step and the position within it.

        Returns:
            tuple[int, Step, float, float]: ``(index, step, seconds into step,
            seconds remaining in step)``.
        """
        index = min(self.step_index(), len(self.workout.steps) - 1)
        st = self.workout.steps[index]
        into = self.elapsed - self._starts[index]
        return index, st, into, max(0.0, st.duration_s - into)

    def next_step(self) -> Step | None:
        """Get the step following the current one.

        Returns:
            Step | None: The next step, or ``None`` if on the last step.
        """
        index = self.step_index() + 1
        return self.workout.steps[index] if index < len(self.workout.steps) else None

    def target_w(self) -> float | None:
        """Compute the current power target, scaled by ``intensity``.

        Returns:
            float | None: Target power in watts, or ``None`` if the workout is
            empty, finished, or the current step is free-ride.
        """
        if not self.workout.steps or self.finished:
            return None
        _, st, into, _ = self.current()
        target = st.target_w(into / st.duration_s if st.duration_s else 0, self.ftp)
        return None if target is None else target * self.intensity

    def skip(self) -> None:
        """Jump to the start of the next step, or to the end of the workout."""
        index = self.step_index() + 1
        self.elapsed = (
            float(self._starts[index])
            if index < len(self._starts)
            else float(self.workout.duration_s)
        )

    def profile(self) -> list[tuple[int, float]]:
        """Build the power profile used to draw the workout chart.

        Returns:
            list[tuple[int, float]]: ``(t, watts)`` points, two per step (start
            and end), with free-ride steps drawn at 0 W.
        """
        pts: list[tuple[int, float]] = []
        for start, st in zip(self._starts, self.workout.steps, strict=True):
            start_w = st.target_w(0, self.ftp) or 0
            end_w = st.target_w(1, self.ftp) or 0
            pts += [(start, start_w), (start + st.duration_s, end_w)]
        return pts
