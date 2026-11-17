"""Sync provider interface. intervals.icu now; Garmin Connect can be added later."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

from smart_trainer.config import Settings
from smart_trainer.core.workout import Workout

# Settings fields an AthleteProfile can update, with their user-facing labels
PROFILE_FIELDS = {
    "ftp": "FTP",
    "weight_kg": "weight",
    "power_zones": "power zones",
    "power_zone_names": "power zone names",
    "hr_zones": "HR zones",
    "lthr": "LTHR",
    "max_hr": "max HR",
}


@dataclass
class AthleteProfile:
    """Rider profile read from a sync provider. ``None``/empty means "unknown".

    Attributes:
        ftp (int | None): FTP in watts.
        weight_kg (float | None): Weight in kilograms.
        power_zones (list[int]): Power zone upper bounds as % of FTP.
        power_zone_names (list[str]): Power zone names.
        hr_zones (list[int]): Heart-rate zone upper bounds in bpm.
        lthr (int | None): Lactate threshold heart rate in bpm.
        max_hr (int | None): Maximum heart rate in bpm.
    """

    ftp: int | None = None
    weight_kg: float | None = None
    power_zones: list[int] = field(default_factory=list)
    power_zone_names: list[str] = field(default_factory=list)
    hr_zones: list[int] = field(default_factory=list)
    lthr: int | None = None
    max_hr: int | None = None

    def apply(self, settings: Settings) -> list[str]:
        """Copy the known values into the settings (does not save them).

        Args:
            settings (Settings): Settings to update in place.

        Returns:
            list[str]: Labels of the fields whose value changed.
        """
        changed: list[str] = []
        for name, label in PROFILE_FIELDS.items():
            value = getattr(self, name)
            if value is None or value == []:
                continue
            if getattr(settings, name) != value:
                setattr(settings, name, value)
                changed.append(label)
        return changed


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

    async def list_workouts(
        self, oldest: date, newest: date, ftp: int, zones: list[int] | None = None
    ) -> list[PlannedWorkout]:
        """List planned workouts in a date range.

        Args:
            oldest (date): First day of the range (inclusive).
            newest (date): Last day of the range (inclusive).
            ftp (int): Rider's FTP in watts, used to resolve targets.
            zones (list[int] | None): Rider's power zone upper bounds (% of FTP).

        Returns:
            list[PlannedWorkout]: Planned workouts sorted by day.
        """

    async def profile(self) -> AthleteProfile:
        """Fetch the rider's profile (FTP, weight, zones).

        Returns:
            AthleteProfile: The rider profile.
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
