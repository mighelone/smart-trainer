"""Export a recorded session to a FIT activity file."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import (
    Activity,
    Event,
    EventType,
    FileType,
    LapTrigger,
    Manufacturer,
    SessionTrigger,
    Sport,
    SubSport,
)

from smart_trainer.core.recorder import Recorder

GAP_S = 5.0  # a hole longer than this (pause, crash) becomes a timer stop/start


def _ms(t: float) -> int:
    """Convert unix seconds to integer milliseconds.

    Args:
        t (float): Unix time in seconds.

    Returns:
        int: Unix time in milliseconds.
    """
    return round(t * 1000)


def _stats(rows) -> dict:
    """Compute summary statistics over samples.

    Args:
        rows (list[sqlite3.Row]): Recorded samples.

    Returns:
        dict: Average/max power, average/max HR and average cadence
        (``None`` where no data).
    """

    def avg(key):
        """Average of a column, ignoring ``None``.

        Args:
            key (str): Column name.

        Returns:
            float | None: The average, or ``None`` if no values.
        """
        v = [r[key] for r in rows if r[key] is not None]
        return sum(v) / len(v) if v else None

    def mx(key):
        """Maximum of a column, ignoring ``None``.

        Args:
            key (str): Column name.

        Returns:
            float | None: The maximum, or ``None`` if no values.
        """
        v = [r[key] for r in rows if r[key] is not None]
        return max(v) if v else None

    return {
        "avg_power": avg("power"),
        "max_power": mx("power"),
        "avg_hr": avg("hr"),
        "max_hr": mx("hr"),
        "avg_cad": avg("cadence"),
    }


def _fill_summary(msg, rows, start: float, end: float, timer_s: float) -> None:
    """Fill the summary fields of a FIT lap or session message.

    Args:
        msg (LapMessage | SessionMessage): Message to fill in place.
        rows (list[sqlite3.Row]): Samples covered by the message.
        start (float): Unix start time.
        end (float): Unix end time.
        timer_s (float): Total timer time in seconds.
    """
    s = _stats(rows)
    msg.start_time = _ms(start)
    msg.timestamp = _ms(end)
    msg.total_elapsed_time = end - start
    msg.total_timer_time = timer_s
    msg.total_distance = (rows[-1]["distance_m"] or 0) - (rows[0]["distance_m"] or 0)
    msg.sport = Sport.CYCLING
    msg.sub_sport = SubSport.INDOOR_CYCLING
    if s["avg_power"] is not None:
        msg.avg_power = round(s["avg_power"])
        msg.max_power = round(s["max_power"])
    if s["avg_hr"] is not None:
        msg.avg_heart_rate = round(s["avg_hr"])
        msg.max_heart_rate = round(s["max_hr"])
    if s["avg_cad"] is not None:
        msg.avg_cadence = round(s["avg_cad"])


def export_fit(rec: Recorder, out_path: Path) -> Path:
    """Export a recorded session to a FIT activity file.

    Paused samples are skipped and gaps longer than ``GAP_S`` become
    timer stop/start events. One lap is written per recorded lap number.

    Args:
        rec (Recorder): The session to export.
        out_path (Path): Destination FIT file path.

    Raises:
        ValueError: If the session has no (unpaused) samples.

    Returns:
        Path: The written FIT file path.
    """
    rows = [r for r in rec.samples() if not r["paused"]]
    if not rows:
        raise ValueError("session has no samples")
    start, end = rows[0]["t"], rows[-1]["t"]

    b = FitFileBuilder(auto_define=True, min_string_size=50)
    fid = FileIdMessage()
    fid.type = FileType.ACTIVITY
    fid.manufacturer = Manufacturer.DEVELOPMENT.value
    fid.product = 0
    fid.serial_number = 0x5354524E
    fid.time_created = _ms(start)
    b.add(fid)

    def event(t: float, et: EventType) -> None:
        """Add a timer event message.

        Args:
            t (float): Unix time of the event.
            et (EventType): Event type (start or stop).
        """
        e = EventMessage()
        e.event = Event.TIMER
        e.event_type = et
        e.timestamp = _ms(t)
        b.add(e)

    event(start, EventType.START)
    timer_s, prev = 0.0, None
    for r in rows:
        if prev is not None:
            gap = r["t"] - prev
            if gap > GAP_S:
                event(prev, EventType.STOP_ALL)
                event(r["t"], EventType.START)
            else:
                timer_s += gap
        m = RecordMessage()
        m.timestamp = _ms(r["t"])
        if r["power"] is not None:
            m.power = round(r["power"])
        if r["hr"] is not None:
            m.heart_rate = int(r["hr"])
        if r["cadence"] is not None:
            m.cadence = round(r["cadence"])
        if r["speed_kmh"] is not None:
            m.speed = r["speed_kmh"] / 3.6
        if r["distance_m"] is not None:
            m.distance = r["distance_m"]
        b.add(m)
        prev = r["t"]
    event(end, EventType.STOP_ALL)

    # Laps: one per recorded lap number (workout step or manual lap)
    laps: dict[int, list] = {}
    for r in rows:
        laps.setdefault(r["lap"], []).append(r)
    for i, (_, lr) in enumerate(sorted(laps.items())):
        lap = LapMessage()
        lap.message_index = i
        lap.event = Event.LAP
        lap.event_type = EventType.STOP
        lap.lap_trigger = LapTrigger.MANUAL
        lt = sum(min(b2["t"] - a["t"], GAP_S) for a, b2 in pairwise(lr)) + 1
        _fill_summary(lap, lr, lr[0]["t"], lr[-1]["t"], lt)
        b.add(lap)

    sess = SessionMessage()
    sess.message_index = 0
    sess.event = Event.SESSION
    sess.event_type = EventType.STOP
    sess.trigger = SessionTrigger.ACTIVITY_END
    sess.first_lap_index = 0
    sess.num_laps = len(laps)
    _fill_summary(sess, rows, start, end, timer_s)
    b.add(sess)

    act = ActivityMessage()
    act.timestamp = _ms(end)
    act.total_timer_time = timer_s
    act.num_sessions = 1
    act.type = Activity.MANUAL
    act.event = Event.ACTIVITY
    act.event_type = EventType.STOP
    b.add(act)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    b.build().to_file(str(out_path))
    return out_path
