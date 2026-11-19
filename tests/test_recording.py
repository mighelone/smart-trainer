import json
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import fitdecode
import pytest

from smart_trainer.core.metrics import normalized_power, training_stress_score
from smart_trainer.core.recorder import Recorder, find_unfinished
from smart_trainer.core.state import Snapshot
from smart_trainer.core.workout import parse_intervals_doc
from smart_trainer.export.fit import export_fit

DOC = json.loads((Path(__file__).parent / "data" / "cadence_endurance_doc.json").read_text())


def test_crash_recovery_after_kill_9(tmp_path):
    """A process recording a ride is SIGKILLed; the session must be found with its samples."""
    script = textwrap.dedent(f"""
        import os, signal, sys, time
        from pathlib import Path
        from smart_trainer.core.recorder import Recorder
        from smart_trainer.core.state import Snapshot
        rec = Recorder.create(Path({str(tmp_path)!r}), "Crashy", 250, None, now=1_700_000_000)
        for i in range(30):
            rec.record(Snapshot(power=200, cadence=90, hr=140, speed_kmh=30), i, 200, False, "erg",
                       now=1_700_000_000 + i)
        os.kill(os.getpid(), signal.SIGKILL)
    """)
    p = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parent.parent)
    assert p.returncode == -signal.SIGKILL
    unfinished = find_unfinished(tmp_path)
    assert len(unfinished) == 1
    info = unfinished[0]
    assert info.name == "Crashy" and info.n_samples == 30 and info.elapsed_s == 29


def test_resume_appends_and_exports_fit(tmp_path):
    wo = parse_intervals_doc(DOC, name="Cadence", ftp=250)
    t0 = 1_700_000_000
    rec = Recorder.create(tmp_path, wo.name, 250, wo, now=t0)
    for i in range(60):
        if i == 30:
            rec.new_lap()
        rec.record(
            Snapshot(power=180 + i, cadence=90, hr=130, speed_kmh=30, balance_left=50),
            i,
            175,
            False,
            "erg",
            now=t0 + i,
        )
    rec.close()  # "crash"

    info = find_unfinished(tmp_path)[0]
    assert info.workout is not None
    assert info.workout.name == "Cadence" and len(info.workout.steps) == len(wo.steps)
    rec = Recorder.open(info.path)
    assert rec.lap == 1 and rec.distance_m > 0
    for i in range(60, 90):  # resumed after a 2 minute gap
        rec.record(
            Snapshot(power=200, cadence=90, hr=135, speed_kmh=30),
            i,
            175,
            False,
            "erg",
            now=t0 + 120 + i,
        )
    rec.record(Snapshot(power=0, cadence=0), 90, 175, True, "erg", now=t0 + 300)  # paused sample
    fit = export_fit(rec, tmp_path / "out.fit")
    rec.set_status("finished")
    assert find_unfinished(tmp_path) == []

    records, laps, sessions, events = [], [], [], []
    with fitdecode.FitReader(fit) as fr:
        for frame in fr:
            if isinstance(frame, fitdecode.FitDataMessage):
                {"record": records, "lap": laps, "session": sessions, "event": events}.get(
                    frame.name, []
                ).append(frame)
    assert len(records) == 90  # paused sample excluded
    assert len(laps) == 2
    s = sessions[0]
    assert s.get_value("avg_power") > 180
    assert s.get_value("total_timer_time") < s.get_value("total_elapsed_time")  # gap not counted
    assert sum(1 for e in events if e.get_value("event_type") == "start") == 2


def test_records_the_mean_power_of_the_second(tmp_path):
    rec = Recorder.create(tmp_path, "Mean", 250, None, now=1_700_000_000)
    rec.record(Snapshot(power=260, power_mean=230, cadence=90), 0, None, False, "erg")
    [row] = rec.samples()
    assert row["power"] == 230  # not the last reading (260)
    rec.close()


def _fit_messages(fit) -> dict[str, list]:
    out: dict[str, list] = {}
    with fitdecode.FitReader(fit) as fr:
        for frame in fr:
            if isinstance(frame, fitdecode.FitDataMessage):
                out.setdefault(frame.name, []).append(frame)
    return out


def test_fit_carries_np_if_tss_and_work(tmp_path):
    t0 = 1_700_000_000
    rec = Recorder.create(tmp_path, "Metrics", 250, None, now=t0)
    for i in range(1200):  # lap 0: 20 min at 250 W (FTP)
        rec.record(Snapshot(power=250, cadence=90), i, 250, False, "erg", now=t0 + i)
    rec.new_lap()
    for i in range(1200, 1220):  # lap 1: 20 s sprint, too short for NP
        rec.record(Snapshot(power=500, cadence=110), i, 500, False, "erg", now=t0 + i)
    fit = export_fit(rec, tmp_path / "metrics.fit")
    rec.close()

    msgs = _fit_messages(fit)
    [session] = msgs["session"]
    powers = [250.0] * 1200 + [500.0] * 20
    np_w = normalized_power(powers)
    assert np_w is not None
    assert session.get_value("normalized_power") == round(np_w)
    assert session.get_value("threshold_power") == 250
    assert session.get_value("total_work") == 250 * 1200 + 500 * 20
    timer_s = session.get_value("total_timer_time")
    assert session.get_value("intensity_factor") == pytest.approx(np_w / 250, abs=0.001)
    tss = training_stress_score(np_w, 250, timer_s)
    assert session.get_value("training_stress_score") == pytest.approx(tss, abs=0.1)

    long_lap, sprint = msgs["lap"]
    assert long_lap.get_value("normalized_power") == 250
    assert long_lap.get_value("total_work") == 250 * 1200
    assert sprint.get_value("total_work") == 500 * 20
    assert sprint.get_value("normalized_power", fallback=None) is None  # under 30 s


def test_fit_without_power_has_no_power_metrics(tmp_path):
    t0 = 1_700_000_000
    rec = Recorder.create(tmp_path, "HR only", 250, None, now=t0)
    for i in range(60):
        rec.record(Snapshot(hr=130, cadence=85), i, None, False, "resistance", now=t0 + i)
    fit = export_fit(rec, tmp_path / "hr.fit")
    rec.close()
    [session] = _fit_messages(fit)["session"]
    for field in ("normalized_power", "total_work", "intensity_factor", "training_stress_score"):
        assert session.get_value(field, fallback=None) is None
