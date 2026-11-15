import base64
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from smart_trainer.sync.intervals import IntervalsClient, IntervalsError

DOC = json.loads((Path(__file__).parent / "data" / "cadence_endurance_doc.json").read_text())
ZWO = (
    "<workout_file><name>x</name><workout>"
    '<SteadyState Duration="600" Power="0.6"/>'
    "</workout></workout_file>"
)


def handler(request: httpx.Request) -> httpx.Response:
    auth = base64.b64decode(request.headers["authorization"].split()[1]).decode()
    if auth != "API_KEY:secret":
        return httpx.Response(401)
    if request.method == "GET" and request.url.path == "/api/v1/athlete/0/events":
        assert request.url.params["category"] == "WORKOUT"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "name": "Cadence",
                    "type": "Ride",
                    "start_date_local": "2026-11-17T09:00:00",
                    "workout_doc": DOC,
                },
                {
                    "id": 2,
                    "name": "Strength",
                    "type": "WeightTraining",
                    "start_date_local": "2026-11-16T09:00:00",
                },
                {
                    "id": 3,
                    "name": "No doc",
                    "type": "Ride",
                    "start_date_local": "2026-11-18T09:00:00",
                },
            ],
        )
    if request.url.path == "/api/v1/athlete/0/events/3/download.zwo":
        return httpx.Response(200, text=ZWO)
    if request.method == "POST" and request.url.path == "/api/v1/athlete/0/activities":
        assert b'filename="ride.fit"' in request.content
        assert request.url.params["paired_event_id"] == "1"
        return httpx.Response(201, json={"id": "i123"})
    return httpx.Response(404)


async def test_list_and_upload(tmp_path):
    c = IntervalsClient("secret", transport=httpx.MockTransport(handler))
    ws = await c.list_workouts(date(2026, 11, 16), date(2026, 11, 18), ftp=250)
    assert [w.name for w in ws] == ["Cadence", "No doc"]
    assert ws[0].workout.source_id == "1" and ws[1].duration_s == 600
    fit = tmp_path / "ride.fit"
    fit.write_bytes(b"FIT")
    assert await c.upload_activity(fit, "Ride", paired_event_id="1") == "i123"
    await c.aclose()


async def test_bad_key():
    c = IntervalsClient("wrong", transport=httpx.MockTransport(handler))
    with pytest.raises(IntervalsError):
        await c.list_workouts(date(2026, 11, 16), date(2026, 11, 18), ftp=250)
    await c.aclose()
