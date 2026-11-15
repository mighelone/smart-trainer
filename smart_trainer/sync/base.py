"""Sync provider interface. intervals.icu now; Garmin Connect can be added later."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from smart_trainer.core.workout import Workout


@dataclass
class PlannedWorkout:
    id: str
    day: date
    name: str
    workout: Workout
    duration_s: int


class SyncProvider(Protocol):
    name: str

    async def list_workouts(self, oldest: date, newest: date, ftp: int) -> list[PlannedWorkout]: ...

    async def upload_activity(self, fit_path: Path, name: str, description: str = "",
                              paired_event_id: str | None = None) -> str: ...
