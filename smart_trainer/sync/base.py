"""Connection interface: training platforms that provide workouts/profile or accept rides."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

from smart_trainer.config import ConnectionSettings, Settings
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


@dataclass
class RideUpload:
    """A finished ride to upload. Each connection uses the fields it understands.

    Attributes:
        fit_path (Path): Path of the FIT file.
        name (str): Activity name.
        description (str): Activity description.
        started_at (float | None): Unix time the ride started.
        workout_source_id (str | None): Id of the planned workout on the platform it came
            from (intervals.icu pairs the activity with that event).
    """

    fit_path: Path
    name: str
    description: str = ""
    started_at: float | None = None
    workout_source_id: str | None = None


class Capability(StrEnum):
    """What a connection can do."""

    UPLOAD = "upload"  # accepts finished rides
    WORKOUTS = "workouts"  # provides planned workouts
    PROFILE = "profile"  # provides FTP, weight and zones


class Connection(ABC):
    """A training platform the app connects to (intervals.icu, Strava, …).

    To add one: subclass it in a new module of ``smart_trainer.sync``, decorate the class
    with :func:`register`, import the module in ``smart_trainer.sync.registry`` and,
    optionally, add a settings card in ``smart_trainer.ui.connections``.

    Implement :meth:`is_configured` and :meth:`test`, plus the method of each declared
    capability: :meth:`upload_activity`, :meth:`list_workouts` or :meth:`profile`.

    Attributes:
        id (str): Stable identifier, used as key in settings, secrets and session files.
        name (str): Human-readable name.
        capabilities (frozenset[Capability]): What the connection can do.
        settings (Settings): Application settings.
    """

    id: ClassVar[str]
    name: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]]

    def __init__(self, settings: Settings) -> None:
        """Initialize the connection.

        Args:
            settings (Settings): Application settings.
        """
        self.settings = settings

    @property
    def conf(self) -> ConnectionSettings:
        """Settings of this connection.

        Returns:
            ConnectionSettings: The connection settings.
        """
        return self.settings.connection(self.id)

    @property
    def options(self) -> dict[str, Any]:
        """Non-secret options of this connection.

        Returns:
            dict[str, Any]: The options (mutable).
        """
        return self.conf.options

    def update_settings(self, **changes: Any) -> None:
        """Validate, apply and save changes to this connection's settings.

        Args:
            **changes (Any): ``enabled``, ``auto_upload`` and/or ``options`` (merged into
                the existing options).

        Raises:
            ValueError: If a field is unknown or a value is invalid (nothing is changed).
        """
        self.settings.update_connection(self.id, **changes)

    def has(self, capability: Capability) -> bool:
        """Whether the connection has a capability.

        Args:
            capability (Capability): The capability.

        Returns:
            bool: ``True`` if supported.
        """
        return capability in self.capabilities

    @property
    def usable(self) -> bool:
        """Whether the connection is enabled and has its credentials.

        Returns:
            bool: ``True`` if it can be used.
        """
        return self.conf.enabled and self.is_configured()

    @abstractmethod
    def is_configured(self) -> bool:
        """Whether the credentials needed to use the connection are present.

        Returns:
            bool: ``True`` if configured.
        """

    @abstractmethod
    async def test(self) -> str:
        """Check the credentials against the service.

        Returns:
            str: The connected account name.
        """

    async def upload_activity(self, ride: RideUpload) -> str:
        """Upload a finished ride (``UPLOAD`` capability).

        Args:
            ride (RideUpload): The ride to upload.

        Returns:
            str: Id of the created activity.
        """
        raise NotImplementedError

    async def list_workouts(
        self, oldest: date, newest: date, ftp: int, zones: list[int] | None = None
    ) -> list[PlannedWorkout]:
        """List planned workouts in a date range (``WORKOUTS`` capability).

        Args:
            oldest (date): First day of the range (inclusive).
            newest (date): Last day of the range (inclusive).
            ftp (int): Rider's FTP in watts, used to resolve targets.
            zones (list[int] | None): Rider's power zone upper bounds (% of FTP).

        Returns:
            list[PlannedWorkout]: Planned workouts sorted by day.
        """
        raise NotImplementedError

    async def profile(self) -> AthleteProfile:
        """Fetch the rider's profile (``PROFILE`` capability).

        Returns:
            AthleteProfile: The rider profile.
        """
        raise NotImplementedError


REGISTRY: dict[str, type[Connection]] = {}


def register[C: type[Connection]](cls: C) -> C:
    """Class decorator that makes a connection available to the app.

    Args:
        cls (type[Connection]): The connection class.

    Returns:
        type[Connection]: The same class.
    """
    REGISTRY[cls.id] = cls
    return cls
