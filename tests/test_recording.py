import json
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import fitdecode

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
