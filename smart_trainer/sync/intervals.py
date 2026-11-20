"""intervals.icu REST client (https://intervals.icu/api/v1, HTTP Basic: API_KEY:<key>)."""

from __future__ import annotations

import logging
from datetime import date
from typing import Self

import httpx

from smart_trainer.core.workout import parse_intervals_doc, parse_zwo
from smart_trainer.keychain import get_api_key, set_api_key
from smart_trainer.sync.base import (
    AthleteProfile,
    Capability,
    Connection,
    PlannedWorkout,
    RideUpload,
    register,
)

log = logging.getLogger(__name__)
BASE_URL = "https://intervals.icu/api/v1"


def _ride_settings(athlete: dict) -> dict:
    """Pick the cycling entry of the athlete's ``sportSettings``.

    Args:
        athlete (dict): intervals.icu athlete JSON object.

    Returns:
        dict: The Ride (else VirtualRide, else first) sport settings, or ``{}``.
    """
    sports = [sport for sport in athlete.get("sportSettings") or [] if isinstance(sport, dict)]
    for wanted in ("Ride", "VirtualRide"):
        for sport in sports:
            if wanted in (sport.get("types") or []):
                return sport
    return sports[0] if sports else {}


def _int_list(values) -> list[int]:
    """Convert a JSON list to ints, or ``[]`` if it isn't a list of numbers."""
    try:
        return [int(round(v)) for v in values or []]
    except TypeError, ValueError:
        return []


def _pos_int(value) -> int | None:
    """Convert a JSON number to a positive int, else ``None``."""
    return round(value) if isinstance(value, int | float) and value > 0 else None


def parse_profile(athlete: dict) -> AthleteProfile:
    """Extract the rider profile from an intervals.icu athlete object.

    FTP is the Ride ``indoor_ftp`` when set (this is an indoor app), else ``ftp``.

    Args:
        athlete (dict): intervals.icu athlete JSON object.

    Returns:
        AthleteProfile: The profile; unknown values are ``None``/empty.
    """
    ride = _ride_settings(athlete)
    weight = athlete.get("icu_weight")
    return AthleteProfile(
        ftp=_pos_int(ride.get("indoor_ftp")) or _pos_int(ride.get("ftp")),
        weight_kg=float(weight) if isinstance(weight, int | float) and weight > 0 else None,
        power_zones=_int_list(ride.get("power_zones")),
        power_zone_names=[str(n) for n in ride.get("power_zone_names") or []],
        hr_zones=_int_list(ride.get("hr_zones")),
        lthr=_pos_int(ride.get("lthr")),
        max_hr=_pos_int(ride.get("max_hr")),
    )


class IntervalsError(RuntimeError):
    """Error returned by the intervals.icu API."""

    pass


class IntervalsClient:
    """Async client for the intervals.icu REST API.

    Attributes:
        name (str): Provider name.
        athlete_id (str): Athlete id; ``"0"`` means the owner of the API key.
    """

    name = "intervals.icu"

    def __init__(
        self,
        api_key: str,
        athlete_id: str = "0",
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            api_key (str): intervals.icu API key.
            athlete_id (str): Athlete id; ``"0"`` means the owner of the API key.
            base_url (str): API base URL.
            transport (httpx.AsyncBaseTransport | None): Custom transport (for tests).
        """
        self.athlete_id = athlete_id
        self._client = httpx.AsyncClient(
            base_url=base_url, auth=("API_KEY", api_key), timeout=30.0, transport=transport
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        """Enter the context.

        Returns:
            Self: This client.
        """
        return self

    async def __aexit__(self, *exc) -> None:
        """Close the client on exit."""
        await self.aclose()

    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        """Send a request and raise on error responses.

        Args:
            method (str): HTTP method.
            url (str): URL relative to the base URL.
            **kw (Any): Extra arguments passed to ``httpx.AsyncClient.request``.

        Raises:
            IntervalsError: If the API key is rejected or the response is an error.

        Returns:
            httpx.Response: The successful response.
        """
        response = await self._client.request(method, url, **kw)
        if response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise IntervalsError("intervals.icu rejected the API key")
        if response.is_error:
            raise IntervalsError(
                f"intervals.icu error {response.status_code}: {response.text[:200]}"
            )
        return response

    async def athlete(self) -> dict:
        """Fetch the athlete profile.

        Returns:
            dict: The athlete JSON object.
        """
        return (await self._request("GET", f"/athlete/{self.athlete_id}")).json()

    async def profile(self) -> AthleteProfile:
        """Fetch the rider's profile (FTP, weight, zones).

        Returns:
            AthleteProfile: The rider profile.
        """
        return parse_profile(await self.athlete())

    async def list_workouts(
        self, oldest: date, newest: date, ftp: int, zones: list[int] | None = None
    ) -> list[PlannedWorkout]:
        """List planned ride workouts in a date range.

        Events without a structured ``workout_doc`` fall back to the ``.zwo`` download.

        Args:
            oldest (date): First day of the range (inclusive).
            newest (date): Last day of the range (inclusive).
            ftp (int): Rider's FTP in watts, used to resolve targets.
            zones (list[int] | None): Rider's power zone upper bounds (% of FTP).

        Returns:
            list[PlannedWorkout]: Planned workouts sorted by day.
        """
        response = await self._request(
            "GET",
            f"/athlete/{self.athlete_id}/events",
            params={
                "oldest": oldest.isoformat(),
                "newest": newest.isoformat(),
                "category": "WORKOUT",
            },
        )
        out: list[PlannedWorkout] = []
        for ev in response.json():
            if ev.get("type") not in (None, "Ride", "VirtualRide"):
                continue
            name = ev.get("name") or "Workout"
            doc = ev.get("workout_doc") or {}
            wo = parse_intervals_doc(doc, name=name, ftp=ftp, source_id=str(ev["id"]), zones=zones)
            if not wo.steps:
                wo = await self._download_zwo(ev["id"], name)
            if wo is None or not wo.steps:
                continue
            day = date.fromisoformat(ev["start_date_local"][:10])
            out.append(PlannedWorkout(str(ev["id"]), day, name, wo, wo.duration_s))
        return sorted(out, key=lambda p: p.day)

    async def _download_zwo(self, event_id, name: str):
        """Fallback when an event has no structured workout_doc.

        Args:
            event_id (int | str): intervals.icu event id.
            name (str): Name to give the workout.

        Returns:
            Workout | None: The parsed workout, or ``None`` if unavailable.
        """
        try:
            response = await self._request(
                "GET", f"/athlete/{self.athlete_id}/events/{event_id}/download.zwo"
            )
            wo = parse_zwo(response.text)
            wo.name, wo.source_id = name, str(event_id)
            return wo
        except Exception as exc:
            log.info("no zwo for event %s: %s", event_id, exc)
            return None

    async def upload_activity(self, ride: RideUpload) -> str:
        """Upload a FIT activity file, paired with its planned workout if any.

        Args:
            ride (RideUpload): The ride to upload.

        Returns:
            str: Id of the created activity.
        """
        params = {"name": ride.name}
        if ride.description:
            params["description"] = ride.description
        if ride.workout_source_id:
            params["paired_event_id"] = ride.workout_source_id
        with ride.fit_path.open("rb") as fh:
            response = await self._request(
                "POST",
                f"/athlete/{self.athlete_id}/activities",
                params=params,
                files={"file": (ride.fit_path.name, fh, "application/octet-stream")},
            )
        data = response.json()
        return str(data.get("id") or data.get("activities", [{}])[0].get("id", ""))


@register
class IntervalsConnection(Connection):
    """intervals.icu: planned workouts, rider profile and ride upload.

    Options: ``athlete_id`` (``"0"`` means the owner of the API key).
    Secret: the API key (``get_api_key``/``set_api_key``).
    """

    id = "intervals"
    name = "intervals.icu"
    capabilities = frozenset({Capability.UPLOAD, Capability.WORKOUTS, Capability.PROFILE})

    @property
    def athlete_id(self) -> str:
        """Configured athlete id.

        Returns:
            str: The athlete id, ``"0"`` by default.
        """
        return str(self.options.get("athlete_id") or "0")

    def is_configured(self) -> bool:
        """Whether an API key is set.

        Returns:
            bool: ``True`` if configured.
        """
        return bool(get_api_key())

    def set_api_key(self, key: str) -> None:
        """Store the API key.

        Args:
            key (str): The API key.
        """
        set_api_key(key)

    def client(self, api_key: str | None = None, athlete_id: str | None = None) -> IntervalsClient:
        """Create an API client.

        Args:
            api_key (str | None): Key to use instead of the stored one.
            athlete_id (str | None): Athlete id to use instead of the configured one.

        Raises:
            IntervalsError: If no API key is configured.

        Returns:
            IntervalsClient: A new client (close it after use).
        """
        key = api_key or get_api_key()
        if not key:
            raise IntervalsError("No intervals.icu API key")
        return IntervalsClient(key, athlete_id or self.athlete_id)

    async def test(self, api_key: str | None = None, athlete_id: str | None = None) -> str:
        """Check the API key by fetching the athlete.

        Args:
            api_key (str | None): Key to test instead of the stored one.
            athlete_id (str | None): Athlete id to test instead of the configured one.

        Returns:
            str: The athlete name.
        """
        async with self.client(api_key, athlete_id) as client:
            return (await client.athlete()).get("name") or "athlete"

    async def upload_activity(self, ride: RideUpload) -> str:
        """Upload a ride.

        Args:
            ride (RideUpload): The ride to upload.

        Returns:
            str: Id of the created activity.
        """
        async with self.client() as client:
            return await client.upload_activity(ride)

    async def list_workouts(
        self, oldest: date, newest: date, ftp: int, zones: list[int] | None = None
    ) -> list[PlannedWorkout]:
        """List planned ride workouts in a date range.

        Args:
            oldest (date): First day of the range (inclusive).
            newest (date): Last day of the range (inclusive).
            ftp (int): Rider's FTP in watts, used to resolve targets.
            zones (list[int] | None): Rider's power zone upper bounds (% of FTP).

        Returns:
            list[PlannedWorkout]: Planned workouts sorted by day.
        """
        async with self.client() as client:
            return await client.list_workouts(oldest, newest, ftp, zones)

    async def profile(self) -> AthleteProfile:
        """Fetch the rider's profile.

        Returns:
            AthleteProfile: The rider profile.
        """
        async with self.client() as client:
            return await client.profile()
