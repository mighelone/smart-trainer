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
    pass


class IntervalsClient:
    name = "intervals.icu"

    def __init__(
        self,
        api_key: str,
        athlete_id: str = "0",
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.athlete_id = athlete_id
        self._client = httpx.AsyncClient(
            base_url=base_url, auth=("API_KEY", api_key), timeout=30.0, transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        r = await self._client.request(method, url, **kw)
        if r.status_code == 401 or r.status_code == 403:
            raise IntervalsError("intervals.icu rejected the API key")
        if r.status_code >= 400:
            raise IntervalsError(f"intervals.icu error {r.status_code}: {r.text[:200]}")
        return r

    async def athlete(self) -> dict:
        return (await self._request("GET", f"/athlete/{self.athlete_id}")).json()

    async def list_workouts(self, oldest: date, newest: date, ftp: int) -> list[PlannedWorkout]:
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
        """Fallback when an event has no structured workout_doc."""
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
