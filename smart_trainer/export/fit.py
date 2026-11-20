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


def _ms(seconds: float) -> int:
    """Convert unix seconds to integer milliseconds.

    Args:
        seconds (float): Unix time in seconds.

    Returns:
        int: Unix time in milliseconds.
    """
    return round(seconds * 1000)


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
        values = [r[key] for r in rows if r[key] is not None]
        return sum(values) / len(values) if values else None

    def mx(key):
        """Maximum of a column, ignoring ``None``.

        Args:
            key (str): Column name.

        Returns:
            float | None: The maximum, or ``None`` if no values.
        """
        values = [r[key] for r in rows if r[key] is not None]
        return max(values) if values else None

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
    stats = _stats(rows)
    msg.start_time = _ms(start)
    msg.timestamp = _ms(end)
    msg.total_elapsed_time = end - start
    msg.total_timer_time = timer_s
    msg.total_distance = (rows[-1]["distance_m"] or 0) - (rows[0]["distance_m"] or 0)
    msg.sport = Sport.CYCLING
    msg.sub_sport = SubSport.INDOOR_CYCLING
    if stats["avg_power"] is not None:
        msg.avg_power = round(stats["avg_power"])
        msg.max_power = round(stats["max_power"])
    if stats["avg_hr"] is not None:
        msg.avg_heart_rate = round(stats["avg_hr"])
        msg.max_heart_rate = round(stats["max_hr"])
    if stats["avg_cad"] is not None:
        msg.avg_cadence = round(stats["avg_cad"])


def _file_id(start: float) -> FileIdMessage:
    """Build the FIT file id message.

    Args:
        start (float): Unix start time of the session.

    Returns:
        FileIdMessage: The file id message.
    """
    fid = FileIdMessage()
    fid.type = FileType.ACTIVITY
    fid.manufacturer = Manufacturer.DEVELOPMENT.value
    fid.product = 0
    fid.serial_number = 0x5354524E
    fid.time_created = _ms(start)
    return fid


def _timer_event(timestamp: float, et: EventType) -> EventMessage:
    """Build a timer event message.

    Args:
        timestamp (float): Unix time of the event.
        et (EventType): Event type (start or stop).

    Returns:
        EventMessage: The timer event.
    """
    event = EventMessage()
    event.event = Event.TIMER
    event.event_type = et
    event.timestamp = _ms(timestamp)
    return event


def _record(row) -> RecordMessage:
    """Build a FIT record message from one sample.

    Args:
        row (sqlite3.Row): Recorded sample.

    Returns:
        RecordMessage: The record, with only the fields the sample has.
    """
    record = RecordMessage()
    record.timestamp = _ms(row["t"])
    if row["power"] is not None:
        record.power = round(row["power"])
    if row["hr"] is not None:
        record.heart_rate = int(row["hr"])
    if row["cadence"] is not None:
        record.cadence = round(row["cadence"])
    if row["speed_kmh"] is not None:
        record.speed = row["speed_kmh"] / 3.6
    if row["distance_m"] is not None:
        record.distance = row["distance_m"]
    return record


def _add_records(builder: FitFileBuilder, rows) -> float:
    """Add the records, with timer stop/start events around gaps longer than ``GAP_S``.

    Args:
        builder (FitFileBuilder): Builder to add the messages to.
        rows (list[sqlite3.Row]): Unpaused samples, in time order.

    Returns:
        float: Total timer time in seconds (gaps excluded).
    """
    builder.add(_timer_event(rows[0]["t"], EventType.START))
    timer_s, prev = 0.0, None
    for row in rows:
        if prev is not None:
            gap = row["t"] - prev
            if gap > GAP_S:
                builder.add(_timer_event(prev, EventType.STOP_ALL))
                builder.add(_timer_event(row["t"], EventType.START))
            else:
                timer_s += gap
        builder.add(_record(row))
        prev = row["t"]
    builder.add(_timer_event(rows[-1]["t"], EventType.STOP_ALL))
    return timer_s


def _laps(rows) -> list[LapMessage]:
    """Build one lap message per recorded lap number (workout step or manual lap).

    Args:
        rows (list[sqlite3.Row]): Unpaused samples, in time order.

    Returns:
        list[LapMessage]: Lap messages, ordered by lap number.
    """
    laps: dict[int, list] = {}
    for row in rows:
        laps.setdefault(row["lap"], []).append(row)
    out = []
    for index, (_, lr) in enumerate(sorted(laps.items())):
        lap = LapMessage()
        lap.message_index = index
        lap.event = Event.LAP
        lap.event_type = EventType.STOP
        lap.lap_trigger = LapTrigger.MANUAL
        lt = sum(min(b["t"] - a["t"], GAP_S) for a, b in pairwise(lr)) + 1
        _fill_summary(lap, lr, lr[0]["t"], lr[-1]["t"], lt)
        out.append(lap)
    return out


def _session(rows, timer_s: float, num_laps: int) -> SessionMessage:
    """Build the session message.

    Args:
        rows (list[sqlite3.Row]): Unpaused samples, in time order.
        timer_s (float): Total timer time in seconds.
        num_laps (int): Number of laps in the session.

    Returns:
        SessionMessage: The session summary.
    """
    sess = SessionMessage()
    sess.message_index = 0
    sess.event = Event.SESSION
    sess.event_type = EventType.STOP
    sess.trigger = SessionTrigger.ACTIVITY_END
    sess.first_lap_index = 0
    sess.num_laps = num_laps
    _fill_summary(sess, rows, rows[0]["t"], rows[-1]["t"], timer_s)
    return sess


def _activity(end: float, timer_s: float) -> ActivityMessage:
    """Build the activity message.

    Args:
        end (float): Unix end time of the session.
        timer_s (float): Total timer time in seconds.

    Returns:
        ActivityMessage: The activity message.
    """
    act = ActivityMessage()
    act.timestamp = _ms(end)
    act.total_timer_time = timer_s
    act.num_sessions = 1
    act.type = Activity.MANUAL
    act.event = Event.ACTIVITY
    act.event_type = EventType.STOP
    return act


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

    builder = FitFileBuilder(auto_define=True, min_string_size=50)
    builder.add(_file_id(rows[0]["t"]))
    timer_s = _add_records(builder, rows)
    laps = _laps(rows)
    for lap in laps:
        builder.add(lap)
    builder.add(_session(rows, timer_s, len(laps)))
    builder.add(_activity(rows[-1]["t"], timer_s))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    builder.build().to_file(str(out_path))
    return out_path
