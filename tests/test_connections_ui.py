"""Connection settings (intervals.icu, Strava) and the Strava OAuth flow."""

import sys
from urllib.parse import parse_qs, urlparse

import pytest
from nicegui import app
from nicegui.testing import User

from smart_trainer.core.engine import Engine
from smart_trainer.keychain import get_secret, set_secret
from smart_trainer.sync import registry
from smart_trainer.ui.context import AppContext
from smart_trainer.ui.strava_auth import STRAVA_CALLBACK, StravaAuthorization

pytestmark = pytest.mark.nicegui_main_file("tests/ui_main.py")


class FakeBrowser:
    """Records the URLs the app opens in the system browser."""

    def __init__(self) -> None:
        self.opened: list[str] = []

    def __call__(self, url: str) -> None:
        self.opened.append(url)

    def state(self) -> str:
        """The ``state`` of the last authorization URL opened."""
        return parse_qs(urlparse(self.opened[-1]).query)["state"][0]


@pytest.fixture
def browser() -> FakeBrowser:
    """A system browser that only records the URLs."""
    return FakeBrowser()


@pytest.fixture
def strava(settings, browser) -> StravaAuthorization:
    """Strava OAuth flow on port 9123, opening URLs in the fake browser."""
    return StravaAuthorization(settings, 9123, open_url=browser)


@pytest.fixture
async def context(user: User, tmp_path, settings, strava):
    """App context with the built-in connections and no devices; its pages are registered."""
    engine = Engine.from_settings(
        settings, sessions=tmp_path, connections=lambda: registry.all_connections(settings)
    )
    ctx = AppContext(engine=engine, settings=settings, strava=strava)
    sys.modules["smart_trainer.ui.pages"].register_pages(ctx)
    async with engine:
        yield ctx


def _text(response) -> str:
    return bytes(response.body).decode()


# --------------------------------------------------------------------- OAuth flow
def test_begin_opens_the_authorization_page_with_the_local_redirect(strava, browser):
    strava.begin("12345")
    query = parse_qs(urlparse(browser.opened[-1]).query)
    assert query["client_id"] == ["12345"]
    assert query["redirect_uri"] == ["http://127.0.0.1:9123/connections/strava/callback"]
    assert query["state"][0]


async def test_finish_refuses_an_unknown_request(strava):
    response = await strava.finish("never-sent", "code", "", "activity:write")
    assert response.status_code == 400
    assert "Strava connection failed" in _text(response)


async def test_a_request_is_accepted_only_once(strava, browser):
    strava.begin("12345")
    first = await strava.finish(browser.state(), "", "access_denied", "")
    second = await strava.finish(browser.state(), "", "access_denied", "")
    assert "Strava connection cancelled" in _text(first)
    assert "Unknown request" in _text(second)


async def test_finish_asks_for_the_upload_permission(strava, browser):
    strava.begin("12345")
    response = await strava.finish(browser.state(), "code", "", "read")
    assert response.status_code == 400
    assert "Missing permission" in _text(response)


async def test_callback_route_is_registered(user: User, context):
    assert STRAVA_CALLBACK in {getattr(route, "path", None) for route in app.routes}


# ------------------------------------------------------------------ settings cards
async def test_settings_page_shows_a_card_per_connection(user: User, context):
    await user.open("/settings")
    await user.should_see("intervals.icu")
    await user.should_see("API key")
    await user.should_see("Strava")
    await user.should_see("Not connected")


async def test_intervals_save_stores_the_athlete_id(user: User, context):
    await user.open("/settings")
    user.find(marker="intervals-athlete").clear().type("i123")
    user.find(marker="intervals-save").click()
    await user.should_see("intervals.icu settings saved")
    assert context.settings.connection("intervals").options["athlete_id"] == "i123"


async def test_strava_connect_needs_the_app_credentials(user: User, context, browser):
    await user.open("/settings")
    user.find("Connect with Strava").click()
    await user.should_see("Enter the Client ID and Client Secret first")
    assert browser.opened == []


async def test_strava_connect_opens_the_browser(user: User, context, browser):
    await user.open("/settings")
    user.find(marker="strava-client-id").type("12345")
    user.find(marker="strava-client-secret").type("s3cret")
    user.find("Connect with Strava").click()
    await user.should_see("Authorize Smart Trainer in the browser window")
    assert len(browser.opened) == 1
    assert get_secret("strava", "client_secret") == "s3cret"


async def test_strava_shows_a_connected_account(user: User, context):
    context.settings.update_connection("strava", options={"client_id": "12345", "athlete": "Eddy"})
    set_secret("strava", "client_secret", "s3cret")
    set_secret("strava", "refresh_token", "token")
    await user.open("/settings")
    await user.should_see("Connected as Eddy")
    await user.should_see("Disconnect")
