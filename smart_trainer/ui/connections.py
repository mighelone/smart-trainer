"""Settings cards of the connections: shared switches plus each connection's own form.

Each connection gets a card with its enable/auto-upload switches. Connection-specific
fields come from the form that :func:`connection_forms` maps to its id; a connection
without a form still gets the generic card.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from nicegui import ui

from smart_trainer.keychain import get_api_key, get_secret, set_secret
from smart_trainer.sync.base import Capability, Connection
from smart_trainer.sync.intervals import IntervalsConnection
from smart_trainer.sync.strava import StravaConnection
from smart_trainer.ui.strava_auth import StravaAuthorization

log = logging.getLogger(__name__)

# Called after credentials change, e.g. to sync the profile right away
OnChange = Callable[[], Awaitable[None]]
# Builds a connection's own form inside its card
FormFactory = Callable[[Connection, OnChange], object]


def connection_forms(authorization: StravaAuthorization) -> dict[str, FormFactory]:
    """The form of each connection that has settings of its own.

    Args:
        authorization (StravaAuthorization): Strava OAuth flow, used by the Strava form.

    Returns:
        dict[str, FormFactory]: Form factory by connection id.
    """
    return {
        IntervalsConnection.id: IntervalsForm,
        StravaConnection.id: lambda conn, _on_change: StravaForm(conn, authorization),
    }


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


class ConnectionCard:
    """Settings card of a connection: enable and auto-upload switches, then its own form."""

    def __init__(self, conn: Connection, on_change: OnChange, form: FormFactory | None) -> None:
        """Build the card.

        Args:
            conn (Connection): The connection.
            on_change (OnChange): Called after the connection's credentials change.
            form (FormFactory | None): Builds the connection's own fields, if it has any.
        """
        self.conn = conn
        with ui.row().classes("items-center w-full mt-2"):
            ui.label(conn.name).classes("section-title")
            ui.space()
            self.enabled = ui.switch("Enabled", value=conn.conf.enabled, on_change=self.persist)
        self.auto = None
        if conn.has(Capability.UPLOAD):
            self.auto = ui.switch(
                "Upload automatically after each ride",
                value=conn.conf.auto_upload,
                on_change=self.persist,
            )
        if form:
            form(conn, on_change)

    def persist(self) -> None:
        """Save the switches."""
        switches = {"enabled": bool(self.enabled.value)}
        if self.auto is not None:
            switches["auto_upload"] = bool(self.auto.value)
        self.conn.update_settings(**switches)


class IntervalsForm:
    """intervals.icu settings: API key and athlete id."""

    def __init__(self, conn: Connection, on_change: OnChange) -> None:
        """Build the form.

        Args:
            conn (Connection): The intervals.icu connection.
            on_change (OnChange): Called after the API key changes.
        """
        assert isinstance(conn, IntervalsConnection)
        self.conn = conn
        self.on_change = on_change
        ui.label(
            "Find your API key at intervals.icu → Settings → Developer Settings. "
            "It is stored in the macOS Keychain."
        ).classes("hint")
        self.key = ui.input(
            "API key",
            password=True,
            password_toggle_button=True,
            placeholder="(saved)" if get_api_key() else "",
        )
        self.athlete = (
            ui.input("Athlete ID", value=conn.athlete_id)
            .tooltip("Leave 0 for your own account")
            .mark("intervals-athlete")
        )
        with ui.row():
            ui.button("Save", icon="save", on_click=self.save).props("color=positive").mark(
                "intervals-save"
            )
            ui.button("Test connection", icon="wifi_tethering", on_click=self.test).props(
                "color=secondary"
            )

    async def save(self) -> None:
        """Store the API key and athlete id."""
        self.conn.update_settings(options={"athlete_id": self.athlete.value.strip() or "0"})
        new_key = self.key.value.strip()
        if new_key:
            self.conn.set_api_key(new_key)
            self.key.value = ""
            self.key.props('placeholder="(saved)"')
        ui.notify("intervals.icu settings saved", type="positive")
        if new_key:
            await self.on_change()

    async def test(self) -> None:
        """Test the typed (or stored) API key."""
        typed = self.key.value.strip()
        if not (typed or get_api_key()):
            ui.notify("Enter an API key first", type="warning")
            return
        await _test(self.conn, api_key=typed or None, athlete_id=self.athlete.value.strip() or "0")


class StravaForm:
    """Strava settings: API application credentials and the OAuth connection."""

    def __init__(self, conn: Connection, authorization: StravaAuthorization) -> None:
        """Build the form.

        Args:
            conn (Connection): The Strava connection.
            authorization (StravaAuthorization): Starts the OAuth flow.
        """
        assert isinstance(conn, StravaConnection)
        self.conn = conn
        self.authorization = authorization
        ui.html(
            "Create your own API application at "
            '<a href="https://www.strava.com/settings/api" target="_blank">'
            "strava.com/settings/api</a> with <b>Authorization Callback Domain</b> "
            "<code>localhost</code>, then enter its Client ID and Client Secret and connect. "
            "The secret is stored in the macOS Keychain."
        ).classes("hint")
        self.client_id = ui.input("Client ID", value=conn.client_id).mark("strava-client-id")
        self.client_secret = ui.input(
            "Client Secret",
            password=True,
            password_toggle_button=True,
            placeholder="(saved)" if get_secret(conn.id, "client_secret") else "",
        ).mark("strava-client-secret")
        self.status = ui.label("").classes("hint")
        self._build_buttons()
        self.refresh()
        ui.timer(2.0, self.refresh)  # picks up the authorization completed in the browser

    def _build_buttons(self) -> None:
        """Build the Connect, Test and Disconnect buttons."""
        with ui.row():
            ui.button("Connect with Strava", icon="link", on_click=self.connect).props(
                "color=positive"
            )
            self.test_btn = ui.button(
                "Test connection", icon="wifi_tethering", on_click=lambda: _test(self.conn)
            ).props("color=secondary")
            self.disconnect_btn = ui.button(
                "Disconnect", icon="link_off", on_click=self.disconnect
            ).props("flat")

    def refresh(self) -> None:
        """Show whether the account is connected."""
        connected = self.conn.is_configured()
        athlete = self.conn.options.get("athlete")
        self.status.set_text(
            f"Connected as {athlete or 'athlete'}" if connected else "Not connected"
        )
        self.disconnect_btn.set_visibility(connected)
        self.test_btn.set_visibility(connected)

    def save_credentials(self) -> bool:
        """Store the typed Client ID and Secret.

        Returns:
            bool: ``True`` if both are now set.
        """
        self.conn.update_settings(options={"client_id": self.client_id.value.strip()})
        if secret := self.client_secret.value.strip():
            set_secret(self.conn.id, "client_secret", secret)
            self.client_secret.value = ""
            self.client_secret.props('placeholder="(saved)"')
        return bool(self.conn.client_id and get_secret(self.conn.id, "client_secret"))

    def connect(self) -> None:
        """Open the Strava authorization page in the system browser."""
        if self.conn.settings.readonly:
            ui.notify("Demo mode: connections can't be changed", type="warning")
            return
        if not self.save_credentials():
            ui.notify("Enter the Client ID and Client Secret first", type="warning")
            return
        self.authorization.begin(self.conn.client_id)
        ui.notify("Authorize Smart Trainer in the browser window that just opened")

    def disconnect(self) -> None:
        """Forget the Strava authorization."""
        self.conn.disconnect()
        self.refresh()
        ui.notify("Disconnected from Strava")
