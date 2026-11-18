"""Settings cards of the connections, and the Strava OAuth callback.

Each connection gets a card with its enable/auto-upload switches. Connection-specific
fields come from a renderer registered with :func:`settings_renderer`. A connection
without a renderer still gets the generic card.
"""

from __future__ import annotations

import logging
import secrets
import webbrowser
from collections.abc import Awaitable, Callable

from fastapi.responses import HTMLResponse
from nicegui import app, ui

from smart_trainer.config import get_api_key, get_secret, set_secret
from smart_trainer.core.engine import Engine
from smart_trainer.sync.base import Capability, Connection
from smart_trainer.sync.intervals import IntervalsConnection
from smart_trainer.sync.strava import StravaConnection, authorize_url

log = logging.getLogger(__name__)

# Called after credentials change, e.g. to sync the profile right away
OnChange = Callable[[], Awaitable[None]]
Renderer = Callable[[Engine, Connection, OnChange], None]
RENDERERS: dict[str, Renderer] = {}
STRAVA_CALLBACK = "/connections/strava/callback"
_strava_states: set[str] = set()  # pending OAuth requests
server_port: int = 8080  # set by main.run(); the OAuth redirect goes to this local port


def settings_renderer(conn_id: str) -> Callable[[Renderer], Renderer]:
    """Register the function that renders a connection's specific settings.

    Args:
        conn_id (str): Connection id.

    Returns:
        Callable[[Renderer], Renderer]: Decorator.
    """

    def deco(fn: Renderer) -> Renderer:
        RENDERERS[conn_id] = fn
        return fn

    return deco


def connection_card(e: Engine, conn: Connection, on_change: OnChange) -> None:
    """Render the settings card of a connection.

    Args:
        e (Engine): The application engine.
        conn (Connection): The connection.
        on_change (OnChange): Called after the connection's credentials change.
    """
    cs = conn.conf

    def persist() -> None:
        """Save the switches."""
        cs.enabled = bool(enabled.value)
        if auto is not None:
            cs.auto_upload = bool(auto.value)
        e.settings.save()

    with ui.row().classes("items-center w-full mt-2"):
        ui.label(conn.name).classes("section-title")
        ui.space()
        enabled = ui.switch("Enabled", value=cs.enabled, on_change=persist)
    auto = None
    if conn.has(Capability.UPLOAD):
        auto = ui.switch(
            "Upload automatically after each ride", value=cs.auto_upload, on_change=persist
        )
    if renderer := RENDERERS.get(conn.id):
        renderer(e, conn, on_change)


async def _test(conn: Connection, **kw) -> None:
    """Check a connection and notify the result.

    Args:
        conn (Connection): The connection.
        **kw (Any): Extra arguments for ``conn.test``.
    """
    try:
        ui.notify(f"Connected as {await conn.test(**kw)}", type="positive")
    except Exception as exc:
        ui.notify(str(exc), type="negative")


# ============================================================ intervals.icu
@settings_renderer(IntervalsConnection.id)
def intervals_settings(e: Engine, conn: Connection, on_change: OnChange) -> None:
    """intervals.icu: API key and athlete id.

    Args:
        e (Engine): The application engine.
        conn (Connection): The intervals.icu connection.
        on_change (OnChange): Called after the API key changes.
    """
    assert isinstance(conn, IntervalsConnection)
    ui.label(
        "Find your API key at intervals.icu → Settings → Developer Settings. "
        "It is stored in the macOS Keychain."
    ).classes("hint")
    key = ui.input(
        "API key",
        password=True,
        password_toggle_button=True,
        placeholder="(saved)" if get_api_key() else "",
    )
    athlete = ui.input("Athlete ID", value=conn.athlete_id).tooltip("Leave 0 for your own account")

    async def save() -> None:
        """Store the API key and athlete id."""
        conn.options["athlete_id"] = athlete.value.strip() or "0"
        new_key = key.value.strip()
        if new_key:
            conn.set_api_key(new_key)
            key.value = ""
            key.props('placeholder="(saved)"')
        e.settings.save()
        ui.notify("intervals.icu settings saved", type="positive")
        if new_key:
            await on_change()

    async def test() -> None:
        """Test the typed (or stored) API key."""
        conn.options["athlete_id"] = athlete.value.strip() or "0"
        if not (key.value.strip() or get_api_key()):
            ui.notify("Enter an API key first", type="warning")
            return
        await _test(conn, api_key=key.value.strip() or None)

    with ui.row():
        ui.button("Save", icon="save", on_click=save).props("color=positive")
        ui.button("Test connection", icon="wifi_tethering", on_click=test).props("color=secondary")


# =================================================================== Strava
def strava_redirect_uri() -> str:
    """Local URL Strava redirects to after authorization.

    Strava accepts any port on ``localhost``/``127.0.0.1``, so the random port of the
    native window works too.

    Returns:
        str: The redirect URI.
    """
    return f"http://127.0.0.1:{server_port}{STRAVA_CALLBACK}"


@settings_renderer(StravaConnection.id)
def strava_settings(e: Engine, conn: Connection, on_change: OnChange) -> None:
    """Strava: API application credentials and the OAuth connection.

    Args:
        e (Engine): The application engine.
        conn (Connection): The Strava connection.
        on_change (OnChange): Called after connecting.
    """
    assert isinstance(conn, StravaConnection)
    ui.html(
        "Create your own API application at "
        '<a href="https://www.strava.com/settings/api" target="_blank">'
        "strava.com/settings/api</a> with <b>Authorization Callback Domain</b> "
        "<code>localhost</code>, then enter its Client ID and Client Secret and connect. "
        "The secret is stored in the macOS Keychain."
    ).classes("hint")
    client_id = ui.input("Client ID", value=conn.client_id)
    client_secret = ui.input(
        "Client Secret",
        password=True,
        password_toggle_button=True,
        placeholder="(saved)" if get_secret(conn.id, "client_secret") else "",
    )
    status = ui.label("").classes("hint")

    def refresh() -> None:
        """Show whether the account is connected."""
        connected = conn.is_configured()
        athlete = conn.options.get("athlete")
        status.set_text(f"Connected as {athlete or 'athlete'}" if connected else "Not connected")
        disconnect_btn.set_visibility(connected)
        test_btn.set_visibility(connected)

    def save_credentials() -> bool:
        """Store the typed Client ID and Secret.

        Returns:
            bool: ``True`` if both are now set.
        """
        conn.options["client_id"] = client_id.value.strip()
        if secret := client_secret.value.strip():
            set_secret(conn.id, "client_secret", secret)
            client_secret.value = ""
            client_secret.props('placeholder="(saved)"')
        e.settings.save()
        return bool(conn.client_id and get_secret(conn.id, "client_secret"))

    def connect() -> None:
        """Open the Strava authorization page in the system browser."""
        if e.settings._readonly:
            ui.notify("Demo mode: connections can't be changed", type="warning")
            return
        if not save_credentials():
            ui.notify("Enter the Client ID and Client Secret first", type="warning")
            return
        state = secrets.token_urlsafe(16)
        _strava_states.add(state)
        url = authorize_url(conn.client_id, strava_redirect_uri(), state)
        # The system browser (not this page): works in the native window and when the
        # page is open on an iPad, since the callback goes to this computer.
        webbrowser.open(url)
        ui.notify("Authorize Smart Trainer in the browser window that just opened")

    def disconnect() -> None:
        """Forget the Strava authorization."""
        conn.disconnect()
        refresh()
        ui.notify("Disconnected from Strava")

    with ui.row():
        ui.button("Connect with Strava", icon="link", on_click=connect).props("color=positive")
        test_btn = ui.button(
            "Test connection", icon="wifi_tethering", on_click=lambda: _test(conn)
        ).props("color=secondary")
        disconnect_btn = ui.button("Disconnect", icon="link_off", on_click=disconnect).props("flat")
    refresh()
    ui.timer(2.0, refresh)  # picks up the authorization completed in the browser


def _page(title: str, text: str, status_code: int = 200) -> HTMLResponse:
    """Small HTML page shown in the browser after the OAuth redirect.

    Args:
        title (str): Heading.
        text (str): Message.
        status_code (int): HTTP status.

    Returns:
        HTMLResponse: The page.
    """
    from html import escape

    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Smart Trainer</title>"
        "<body style='font-family:system-ui;background:#0f1418;color:#e6edf3;"
        "display:grid;place-items:center;height:100vh;margin:0'>"
        f"<div style='text-align:center'><h2>{escape(title)}</h2><p>{escape(text)}</p></div>",
        status_code=status_code,
    )


@app.get(STRAVA_CALLBACK)
async def strava_callback(
    state: str = "", code: str = "", error: str = "", scope: str = ""
) -> HTMLResponse:
    """Finish the Strava OAuth flow: trade the code for tokens and store them.

    Args:
        state (str): The value sent with the authorization request.
        code (str): Authorization code.
        error (str): Set by Strava when the user denied access.
        scope (str): Scopes the user granted.

    Returns:
        HTMLResponse: A page telling the user the outcome.
    """
    from smart_trainer.ui.pages import S

    if state not in _strava_states:
        return _page("Strava connection failed", "Unknown request: connect again.", 400)
    _strava_states.discard(state)
    if error or not code:
        return _page("Strava connection cancelled", "Smart Trainer was not authorized.", 400)
    if "activity:write" not in scope.split(","):
        return _page(
            "Missing permission",
            "Allow 'Upload your activities' so rides can be uploaded. Connect again.",
            400,
        )
    conn = StravaConnection(S.engine.settings)
    try:
        name = await conn.authorize(code)
    except Exception as exc:
        log.warning("Strava authorization failed: %s", exc)
        return _page("Strava connection failed", str(exc), 400)
    log.info("connected to Strava as %s", name)
    return _page(f"Connected to Strava as {name}", "You can close this tab.")
