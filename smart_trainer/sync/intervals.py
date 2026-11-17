"""intervals.icu REST client (https://intervals.icu/api/v1, HTTP Basic: API_KEY:<key>)."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import httpx

from smart_trainer.core.workout import parse_intervals_doc, parse_zwo
from smart_trainer.sync.base import PlannedWorkout

log = logging.getLogger(__name__)
BASE_URL = "https://intervals.icu/api/v1"


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
        r = await self._client.request(method, url, **kw)
        if r.status_code == 401 or r.status_code == 403:
            raise IntervalsError("intervals.icu rejected the API key")
        if r.status_code >= 400:
            raise IntervalsError(f"intervals.icu error {r.status_code}: {r.text[:200]}")
        return r

    async def athlete(self) -> dict:
        """Fetch the athlete profile.

        Returns:
            dict: The athlete JSON object.
        """
        return (await self._request("GET", f"/athlete/{self.athlete_id}")).json()

    async def list_workouts(self, oldest: date, newest: date, ftp: int) -> list[PlannedWorkout]:
        """List planned ride workouts in a date range.

        Events without a structured ``workout_doc`` fall back to the ``.zwo`` download.

        Args:
            oldest (date): First day of the range (inclusive).
            newest (date): Last day of the range (inclusive).
            ftp (int): Rider's FTP in watts, used to resolve targets.

        Returns:
            list[PlannedWorkout]: Planned workouts sorted by day.
        """
        r = await self._request(
            "GET",
            f"/athlete/{self.athlete_id}/events",
            params={
                "oldest": oldest.isoformat(),
                "newest": newest.isoformat(),
                "category": "WORKOUT",
            },
        )
        out: list[PlannedWorkout] = []
        for ev in r.json():
            if ev.get("type") not in (None, "Ride", "VirtualRide"):
                continue
            name = ev.get("name") or "Workout"
            doc = ev.get("workout_doc") or {}
            wo = parse_intervals_doc(doc, name=name, ftp=ftp, source_id=str(ev["id"]))
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
            r = await self._request(
                "GET", f"/athlete/{self.athlete_id}/events/{event_id}/download.zwo"
            )
            wo = parse_zwo(r.text)
            wo.name, wo.source_id = name, str(event_id)
            return wo
        except Exception as exc:
            log.info("no zwo for event %s: %s", event_id, exc)
            return None

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
        params = {"name": name}
        if description:
            params["description"] = description
        if paired_event_id:
            params["paired_event_id"] = paired_event_id
        with fit_path.open("rb") as fh:
            r = await self._request(
                "POST",
                f"/athlete/{self.athlete_id}/activities",
                params=params,
                files={"file": (fit_path.name, fh, "application/octet-stream")},
            )
        data = r.json()
        return str(data.get("id") or data.get("activities", [{}])[0].get("id", ""))
