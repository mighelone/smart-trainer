import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import smart_trainer.sync.strava as strava_mod
from smart_trainer.config import Settings
from smart_trainer.sync.base import RideUpload
from smart_trainer.sync.strava import StravaClient, StravaConnection, StravaError, authorize_url


def _token(request: httpx.Request, refresh_out="rt-2") -> httpx.Response:
    form = parse_qs(request.content.decode())
    assert form["client_id"] == ["42"] and form["client_secret"] == ["shh"]
    if form["grant_type"] == ["refresh_token"]:
        if form["refresh_token"] != ["rt-1"]:
            return httpx.Response(400, json={"message": "Bad Request"})
    else:
        assert form["code"] == ["the-code"]
    return httpx.Response(
        200,
        json={
            "access_token": "at",
            "expires_at": time.time() + 3600,
            "refresh_token": refresh_out,
            "athlete": {"firstname": "Ada", "lastname": "Rider"},
        },
    )


class Api:
    """Fake Strava API; ``upload_states`` are returned in turn by the upload polls."""

    def __init__(self, upload_states):
        self.upload_states = list(upload_states)
        self.uploads = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return _token(request)
        if request.headers.get("authorization") != "Bearer at":
            return httpx.Response(401)
        if request.method == "POST" and request.url.path == "/api/v3/uploads":
            self.uploads.append(request.content)
            return httpx.Response(201, json={"id": 7, **self.upload_states.pop(0)})
        if request.method == "GET" and request.url.path == "/api/v3/uploads/7":
            return httpx.Response(200, json={"id": 7, **self.upload_states.pop(0)})
        if request.url.path == "/api/v3/athlete":
            return httpx.Response(200, json={"firstname": "Ada", "lastname": "Rider"})
        return httpx.Response(404)


def _client(api, refresh="rt-1") -> StravaClient:
    return StravaClient(
        "42",
        "shh",
        refresh,
        transport=httpx.MockTransport(api),
        poll_interval=0,
        poll_timeout=5,
    )


@pytest.fixture
def ride(tmp_path):
    fit = tmp_path / "session-20261117-090000.fit"
    fit.write_bytes(b"FIT")
    return RideUpload(fit, "Morning ride", workout_source_id="99")


def test_authorize_url():
    q = parse_qs(urlparse(authorize_url("42", "http://127.0.0.1:8080/cb", "xyz")).query)
    assert q["client_id"] == ["42"] and q["state"] == ["xyz"]
    assert q["redirect_uri"] == ["http://127.0.0.1:8080/cb"]
    assert "activity:write" in q["scope"][0].split(",")


async def test_upload_polls_until_processed_and_rotates_token(ride):
    api = Api([{"activity_id": None}, {"activity_id": None}, {"activity_id": 12345}])
    async with _client(api) as c:
        assert await c.upload_activity(ride) == "12345"
        assert c.refresh_token == "rt-2"  # Strava rotated it
    body = api.uploads[0]
    assert b'name="data_type"\r\n\r\nfit' in body
    assert b'name="trainer"\r\n\r\n1' in body
    assert b'name="external_id"\r\n\r\nsession-20261117-090000' in body
    assert b'filename="session-20261117-090000.fit"' in body


async def test_duplicate_counts_as_uploaded(ride):
    error = "session.fit duplicate of <a href='/activities/555' target='_blank'>activity</a>"
    async with _client(Api([{"error": error}])) as c:
        assert await c.upload_activity(ride) == "555"


async def test_processing_error(ride):
    async with _client(Api([{"error": "Corrupt file"}])) as c:
        with pytest.raises(StravaError, match="Corrupt file"):
            await c.upload_activity(ride)


async def test_bad_refresh_token(ride):
    async with _client(Api([]), refresh="revoked") as c:
        with pytest.raises(StravaError, match="reconnect"):
            await c.upload_activity(ride)


async def test_not_connected(ride):
    async with _client(Api([]), refresh=None) as c:
        with pytest.raises(StravaError, match="Not connected"):
            await c.upload_activity(ride)


async def test_expired_authorization(ride):
    def api(request):
        if request.url.path == "/oauth/token":
            return _token(request)
        return httpx.Response(401)

    async with _client(api) as c:
        with pytest.raises(StravaError, match="expired"):
            await c.upload_activity(ride)


# ------------------------------------------------------------- connection
@pytest.fixture
def secrets(monkeypatch, tmp_path):
    store = {"client_secret": "shh", "refresh_token": "rt-1"}
    monkeypatch.setattr(strava_mod, "get_secret", lambda conn, name: store.get(name))
    monkeypatch.setattr(strava_mod, "set_secret", lambda conn, name, v: store.update({name: v}))
    monkeypatch.setattr(strava_mod, "delete_secret", lambda conn, name: store.pop(name, None))
    monkeypatch.setattr(Settings, "path", classmethod(lambda cls: tmp_path / "config.json"))
    real = StravaClient
    api = Api([{"activity_id": 1}])
    monkeypatch.setattr(
        strava_mod,
        "StravaClient",
        lambda *a, **kw: real(*a, transport=httpx.MockTransport(api), poll_interval=0),
    )
    return store


async def test_connection_lifecycle(secrets, ride):
    st = Settings()
    conn = StravaConnection(st)
    assert not conn.is_configured()  # no client id yet
    st.connection("strava").options["client_id"] = "42"
    assert conn.is_configured()
    assert await conn.upload_activity(ride) == "1"
    assert secrets["refresh_token"] == "rt-2"  # rotated token stored
    secrets["refresh_token"] = "rt-1"
    assert await conn.authorize("the-code") == "Ada Rider"
    assert Settings.load().connections["strava"].options["athlete"] == "Ada Rider"
    conn.disconnect()
    assert not conn.is_configured() and "athlete" not in conn.options
