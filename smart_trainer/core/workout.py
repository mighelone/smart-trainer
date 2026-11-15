"""Workout model, parsers (intervals.icu workout_doc, Zwift .zwo) and runner."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field

# Mid-point of Coggan power zones (fraction of FTP) for zone-based targets
ZONE_MID = {1: 0.50, 2: 0.65, 3: 0.82, 4: 0.97, 5: 1.12, 6: 1.35, 7: 1.60}


@dataclass
class Step:
    duration_s: int
    start: float  # fraction of FTP (unit="ftp") or watts (unit="watts")
    end: float | None = None  # ramp end; None = steady
    unit: str = "ftp"
    text: str = ""
    free_ride: bool = False
    cadence: int | None = None

    def target_w(self, frac: float, ftp: int) -> float | None:
        if self.free_ride:
            return None
        end = self.start if self.end is None else self.end
        v = self.start + (end - self.start) * frac
        return v * ftp if self.unit == "ftp" else v


@dataclass
class Workout:
    name: str
    steps: list[Step] = field(default_factory=list)
    description: str = ""
    source_id: str | None = None  # e.g. intervals.icu event id

    @property
    def duration_s(self) -> int:
        return sum(s.duration_s for s in self.steps)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Workout":
        return cls(name=d["name"], steps=[Step(**s) for s in d.get("steps", [])],
                   description=d.get("description", ""), source_id=d.get("source_id"))

    def step_starts(self) -> list[int]:
        out, t = [], 0
        for s in self.steps:
            out.append(t)
            t += s.duration_s
        return out


# --------------------------------------------------------------- intervals.icu
def _icu_power(p: dict | None, ftp: int) -> tuple[float, float | None, str] | None:
    """Return (start, end, unit) from an intervals.icu power target."""
    if not p:
        return None
    units = (p.get("units") or "%ftp").lower()
    start = p.get("start", p.get("value"))
    end = p.get("end") if "start" in p else None
    if start is None:
        return None
    if units in ("%ftp", "percent_ftp"):
        return start / 100.0, (end / 100.0 if end is not None else None), "ftp"
    if units in ("w", "watts"):
        return float(start), (float(end) if end is not None else None), "watts"
    if units in ("power_zone", "zone"):
        s = ZONE_MID.get(int(round(start)), 0.6)
        e = ZONE_MID.get(int(round(end)), s) if end is not None else None
        return s, e, "ftp"
    return None


def parse_intervals_doc(doc: dict, name: str = "Workout", ftp: int = 250,
                        source_id: str | None = None) -> Workout:
    steps: list[Step] = []

    def walk(items: list[dict]) -> None:
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
            pw = _icu_power(it.get("power"), ftp)
            cad = it.get("cadence", {}) or {}
            cadence = cad.get("value") or cad.get("start")
            text = it.get("text") or ""
            if it.get("freeride") or pw is None:
                steps.append(Step(dur, 0.0, free_ride=True, text=text))
            else:
                start, end, unit = pw
                if not it.get("ramp") and end is not None:
                    start, end = (start + end) / 2, None  # range target -> midpoint
                steps.append(Step(dur, start, end, unit, text=text,
                                  cadence=int(cadence) if cadence else None))

    walk(doc.get("steps", []))
    return Workout(name=name, steps=steps, description=doc.get("description", "") or "",
                   source_id=source_id)


# ------------------------------------------------------------------ Zwift .zwo
def parse_zwo(xml_text: str) -> Workout:
    root = ET.fromstring(xml_text)
    name = (root.findtext("name") or "ZWO workout").strip()
    desc = (root.findtext("description") or "").strip()
    steps: list[Step] = []
    container = root.find("workout")
    if container is None:
        raise ValueError("no <workout> element")
    for el in container:
        tag = el.tag.lower()
        a = el.attrib
        f = lambda k, d=0.0: float(a.get(k, d))  # noqa: E731
        cad = int(f("Cadence")) if "Cadence" in a else None
        if tag == "steadystate":
            steps.append(Step(int(f("Duration")), f("Power"), cadence=cad))
        elif tag in ("warmup", "cooldown", "ramp"):
            steps.append(Step(int(f("Duration")), f("PowerLow"), f("PowerHigh"), cadence=cad))
        elif tag == "intervalst":
            for _ in range(int(f("Repeat", 1))):
                steps.append(Step(int(f("OnDuration")), f("OnPower"), cadence=cad))
                steps.append(Step(int(f("OffDuration")), f("OffPower")))
        elif tag in ("freeride", "maxeffort"):
            steps.append(Step(int(f("Duration")), 0.0, free_ride=True))
    return Workout(name=name, steps=steps, description=desc)


# --------------------------------------------------------------------- runner
class WorkoutRunner:
    """Tracks workout progress in *active* seconds (paused time doesn't count)."""

    def __init__(self, workout: Workout, ftp: int, elapsed_s: float = 0.0) -> None:
        self.workout = workout
        self.ftp = ftp
        self.elapsed = float(elapsed_s)
        self.intensity = 1.0
        self.paused = False
        self._starts = workout.step_starts()

    @property
    def finished(self) -> bool:
        return self.elapsed >= self.workout.duration_s

    def advance(self, dt: float) -> None:
        if not self.paused and not self.finished:
            self.elapsed = min(self.elapsed + dt, float(self.workout.duration_s))

    def step_index(self) -> int:
        for i in range(len(self._starts) - 1, -1, -1):
            if self.elapsed >= self._starts[i]:
                return i
        return 0

    def current(self) -> tuple[int, Step, float, float]:
        """(index, step, seconds into step, seconds remaining in step)."""
        i = min(self.step_index(), len(self.workout.steps) - 1)
        st = self.workout.steps[i]
        into = self.elapsed - self._starts[i]
        return i, st, into, max(0.0, st.duration_s - into)

    def next_step(self) -> Step | None:
        i = self.step_index() + 1
        return self.workout.steps[i] if i < len(self.workout.steps) else None

    def target_w(self) -> float | None:
        if not self.workout.steps or self.finished:
            return None
        _, st, into, _ = self.current()
        t = st.target_w(into / st.duration_s if st.duration_s else 0, self.ftp)
        return None if t is None else t * self.intensity

    def skip(self) -> None:
        i = self.step_index() + 1
        self.elapsed = float(self._starts[i]) if i < len(self._starts) else float(self.workout.duration_s)

    def profile(self) -> list[tuple[int, float]]:
        """(t, watts) points for drawing the workout chart."""
        pts: list[tuple[int, float]] = []
        for start, st in zip(self._starts, self.workout.steps):
            a = st.target_w(0, self.ftp) or 0
            b = st.target_w(1, self.ftp) or 0
            pts += [(start, a), (start + st.duration_s, b)]
        return pts
