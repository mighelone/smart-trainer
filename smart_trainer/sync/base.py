"""Sync provider interface. intervals.icu now; Garmin Connect can be added later."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from smart_trainer.core.workout import Workout


@dataclass
class PlannedWorkout:
    """A workout planned on the calendar of a sync provider.

    Attributes:
        id (str): Provider event id.
        day (date): Planned day.
        name (str): Workout name.
        workout (Workout): The parsed workout.
        duration_s (int): Workout duration in seconds.
    """

    id: str
    day: date
    name: str
    workout: Workout
    duration_s: int


class SyncProvider(Protocol):
    """Interface for training platforms that provide workouts and accept activities.

    Attributes:
        name (str): Human-readable provider name.
    """

    name: str

    async def list_workouts(self, oldest: date, newest: date, ftp: int) -> list[PlannedWorkout]:
        """List planned workouts in a date range.

        Args:
            oldest (date): First day of the range (inclusive).
            newest (date): Last day of the range (inclusive).
            ftp (int): Rider's FTP in watts, used to resolve targets.

        Returns:
            list[PlannedWorkout]: Planned workouts sorted by day.
        """

    async def upload_activity(
        self, fit_path: Path, name: str, description: str = "", paired_event_id: str | None = None
    ) -> str:
        """Upload a FIT activity file.

        Args:
            fit_path (Path): Path of the FIT file.
            name (str): Activity name.
            description (str): Activity description.
            paired_event_id (str | None): Planned event to pair the activity with.

        Returns:
            str: Id of the created activity.
        """
