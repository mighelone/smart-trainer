"""Strava OAuth flow: open the authorization page and handle the redirect back to the app."""

from __future__ import annotations

import logging
import secrets
import webbrowser
from collections.abc import Callable
from html import escape

from fastapi.responses import HTMLResponse
from nicegui import app

from smart_trainer.config import Settings
from smart_trainer.sync.strava import StravaConnection, authorize_url

log = logging.getLogger(__name__)

STRAVA_CALLBACK = "/connections/strava/callback"


def _page(title: str, text: str, status_code: int = 200) -> HTMLResponse:
    """Small HTML page shown in the browser after the OAuth redirect.

    Args:
        title (str): Heading.
        text (str): Message.
        status_code (int): HTTP status.

    Returns:
        HTMLResponse: The page.
    """
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Smart Trainer</title>"
        "<body style='font-family:system-ui;background:#0f1418;color:#e6edf3;"
        "display:grid;place-items:center;height:100vh;margin:0'>"
        f"<div style='text-align:center'><h2>{escape(title)}</h2><p>{escape(text)}</p></div>",
        status_code=status_code,
    )


class StravaAuthorization:
    """Strava OAuth requests: starts them in the browser and finishes them on the redirect.

    Each request carries a random ``state`` that the redirect must echo back once, so a
    callback that this app didn't start is refused.
    """

    def __init__(
        self,
        settings: Settings,
        port: int,
        open_url: Callable[[str], object] = webbrowser.open,
    ) -> None:
        """Set up the flow.

        Args:
            settings (Settings): User settings, where the Strava connection is stored.
            port (int): Local port of the app's server, where the redirect comes back.
            open_url (Callable[[str], object]): Opens a URL in the system browser.
        """
        self.settings = settings
        self.port = port
        self._open_url = open_url
        self._pending: set[str] = set()

    @property
    def redirect_uri(self) -> str:
        """Local URL Strava redirects to after authorization.

        Strava accepts any port on ``localhost``/``127.0.0.1``, so the random port of the
        native window works too.

        Returns:
            str: The redirect URI.
        """
        return f"http://127.0.0.1:{self.port}{STRAVA_CALLBACK}"

    def register_route(self) -> None:
        """Add the route the Strava OAuth redirect comes back to."""

        @app.get(STRAVA_CALLBACK)
        async def strava_callback(
            state: str = "", code: str = "", error: str = "", scope: str = ""
        ) -> HTMLResponse:
            """Finish the Strava OAuth flow.

            Args:
                state (str): The value sent with the authorization request.
                code (str): Authorization code.
                error (str): Set by Strava when the user denied access.
                scope (str): Scopes the user granted.

            Returns:
                HTMLResponse: A page telling the user the outcome.
            """
            return await self.finish(state, code, error, scope)

    def begin(self, client_id: str) -> None:
        """Open the Strava authorization page in the system browser.

        The system browser, not the app's page: it works in the native window and when the
        page is open on an iPad, since the redirect comes back to this computer.

        Args:
            client_id (str): Client ID of the user's Strava API application.
        """
        state = secrets.token_urlsafe(16)
        self._pending.add(state)
        self._open_url(authorize_url(client_id, self.redirect_uri, state))

    async def finish(self, state: str, code: str, error: str, scope: str) -> HTMLResponse:
        """Trade the OAuth code for tokens and store them.

        Args:
            state (str): The value sent with the authorization request.
            code (str): Authorization code.
            error (str): Set by Strava when the user denied access.
            scope (str): Scopes the user granted.

        Returns:
            HTMLResponse: A page telling the user the outcome.
        """
        if state not in self._pending:
            return _page("Strava connection failed", "Unknown request: connect again.", 400)
        self._pending.discard(state)
        if error or not code:
            return _page("Strava connection cancelled", "Smart Trainer was not authorized.", 400)
        if "activity:write" not in scope.split(","):
            return _page(
                "Missing permission",
                "Allow 'Upload your activities' so rides can be uploaded. Connect again.",
                400,
            )
        conn = StravaConnection(self.settings)
        try:
            name = await conn.authorize(code)
        except Exception as exc:
            log.warning("Strava authorization failed: %s", exc)
            return _page("Strava connection failed", str(exc), 400)
        log.info("connected to Strava as %s", name)
        return _page(f"Connected to Strava as {name}", "You can close this tab.")
