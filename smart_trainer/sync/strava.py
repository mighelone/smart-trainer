"""Strava: upload finished rides (https://developers.strava.com/docs/reference/).

Strava uses OAuth 2. Each user creates their own API application at
https://www.strava.com/settings/api (Authorization Callback Domain: ``localhost``) and enters
its Client ID and Client Secret in Settings. "Connect with Strava" then authorizes the app in
the browser; the callback (``smart_trainer.ui.connections``) stores the refresh token in the
keyring. Every API session trades the refresh token for a short-lived access token.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Self
from urllib.parse import urlencode

import httpx

from smart_trainer.keychain import delete_secret, get_secret, set_secret
from smart_trainer.sync.base import Capability, Connection, RideUpload, register

log = logging.getLogger(__name__)
API_URL = "https://www.strava.com/api/v3"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
SCOPE = "read,activity:write"
_DUPLICATE = re.compile(r"duplicate of\D*(\d+)")


class StravaError(RuntimeError):
    """Error returned by the Strava API."""

    pass


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Build the URL where the user authorizes the app.

    Args:
        client_id (str): Strava API application Client ID.
        redirect_uri (str): Where Strava sends the user back with the code.
        state (str): Opaque value echoed back, to match the callback with the request.

    Returns:
        str: The authorization URL.
    """
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPE,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def athlete_name(athlete: dict) -> str:
    """Display name of a Strava athlete object.

    Args:
        athlete (dict): Strava athlete JSON object.

    Returns:
        str: First and last name, or ``"athlete"``.
    """
    name = f"{athlete.get('firstname') or ''} {athlete.get('lastname') or ''}".strip()
    return name or "athlete"


class StravaClient:
    """Async client for the Strava API.

    Attributes:
        refresh_token (str | None): Current refresh token; Strava may rotate it, so
            callers should store it again after use.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str | None = None,
        *,
        api_url: str = API_URL,
        token_url: str = TOKEN_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        poll_interval: float = 1.0,
        poll_timeout: float = 60.0,
    ) -> None:
        """Initialize the client.

        Args:
            client_id (str): Strava API application Client ID.
            client_secret (str): Strava API application Client Secret.
            refresh_token (str | None): Refresh token from a previous authorization.
            api_url (str): API base URL.
            token_url (str): OAuth token endpoint.
            transport (httpx.AsyncBaseTransport | None): Custom transport (for tests).
            poll_interval (float): Seconds between upload status checks.
            poll_timeout (float): Maximum seconds to wait for an upload to be processed.
        """
        self.client_id, self.client_secret = client_id, client_secret
        self.refresh_token = refresh_token
        self._token_url = token_url
        self._access: str | None = None
        self._expires_at = 0.0
        self._poll_interval, self._poll_timeout = poll_interval, poll_timeout
        self._client = httpx.AsyncClient(base_url=api_url, timeout=30.0, transport=transport)

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

    # ------------------------------------------------------------------ OAuth
    async def _token(self, **params: str) -> dict[str, Any]:
        """Call the token endpoint and keep the returned tokens.

        Args:
            **params (str): Grant-specific form fields.

        Raises:
            StravaError: If Strava rejects the request.

        Returns:
            dict[str, Any]: The token response.
        """
        r = await self._client.post(
            self._token_url,
            data={"client_id": self.client_id, "client_secret": self.client_secret, **params},
        )
        if r.status_code in {
            httpx.codes.BAD_REQUEST,
            httpx.codes.UNAUTHORIZED,
            httpx.codes.FORBIDDEN,
        }:
            raise StravaError(
                "Strava rejected the authorization: check the Client ID/Secret and "
                "reconnect in Settings"
            )
        if r.is_error:
            raise StravaError(f"Strava error {r.status_code}: {r.text[:200]}")
        data = r.json()
        self._access = data["access_token"]
        self._expires_at = float(data.get("expires_at") or 0)
        self.refresh_token = data.get("refresh_token") or self.refresh_token
        return data

    async def exchange_code(self, code: str) -> dict[str, Any]:
        """Trade the authorization code from the callback for tokens.

        Args:
            code (str): The ``code`` query parameter of the callback.

        Returns:
            dict[str, Any]: The token response, including the ``athlete`` summary.
        """
        return await self._token(code=code, grant_type="authorization_code")

    async def _access_token(self) -> str:
        """Return a valid access token, refreshing it when needed.

        Raises:
            StravaError: If not authorized.

        Returns:
            str: The access token.
        """
        if self._access and self._expires_at - 60 > time.time():
            return self._access
        if not self.refresh_token:
            raise StravaError("Not connected to Strava")
        await self._token(refresh_token=self.refresh_token, grant_type="refresh_token")
        assert self._access is not None
        return self._access

    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        """Send an authorized API request and raise on error responses.

        Args:
            method (str): HTTP method.
            url (str): URL relative to the API base URL.
            **kw (Any): Extra arguments passed to ``httpx.AsyncClient.request``.

        Raises:
            StravaError: If the response is an error.

        Returns:
            httpx.Response: The successful response.
        """
        headers = {"Authorization": f"Bearer {await self._access_token()}"}
        r = await self._client.request(method, url, headers=headers, **kw)
        if r.status_code == httpx.codes.UNAUTHORIZED:
            raise StravaError("Strava authorization expired or revoked: reconnect in Settings")
        if r.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise StravaError("Strava rate limit reached: try again later")
        if r.is_error:
            raise StravaError(f"Strava error {r.status_code}: {r.text[:200]}")
        return r

    # -------------------------------------------------------------------- API
    async def athlete(self) -> dict[str, Any]:
        """Fetch the authorized athlete.

        Returns:
            dict[str, Any]: The athlete JSON object.
        """
        return (await self._request("GET", "/athlete")).json()

    async def upload_activity(self, ride: RideUpload) -> str:
        """Upload a FIT file and wait until Strava has processed it.

        A file Strava already has (a duplicate) counts as uploaded.

        Args:
            ride (RideUpload): The ride to upload.

        Raises:
            StravaError: If Strava rejects the file or processing times out.

        Returns:
            str: Id of the created (or existing) activity.
        """
        data = {
            "data_type": "fit",
            "name": ride.name,
            "trainer": "1",
            "external_id": ride.fit_path.stem,
        }
        if ride.description:
            data["description"] = ride.description
        with ride.fit_path.open("rb") as fh:
            r = await self._request(
                "POST",
                "/uploads",
                data=data,
                files={"file": (ride.fit_path.name, fh, "application/octet-stream")},
            )
        status = r.json()
        deadline = time.monotonic() + self._poll_timeout
        while True:
            if status.get("activity_id"):
                return str(status["activity_id"])
            if error := status.get("error"):
                if m := _DUPLICATE.search(str(error)):
                    log.info("Strava already has this ride: activity %s", m.group(1))
                    return m.group(1)
                raise StravaError(f"Strava could not process the ride: {error}")
            if time.monotonic() > deadline:
                raise StravaError("Strava is still processing the ride: check strava.com later")
            await asyncio.sleep(self._poll_interval)
            status = (await self._request("GET", f"/uploads/{status['id']}")).json()


@register
class StravaConnection(Connection):
    """Strava: ride upload.

    Options: ``client_id`` and ``athlete`` (name of the connected athlete).
    Secrets: ``client_secret`` and ``refresh_token``.
    """

    id = "strava"
    name = "Strava"
    capabilities = frozenset({Capability.UPLOAD})

    @property
    def client_id(self) -> str:
        """Configured Client ID.

        Returns:
            str: The Client ID, or ``""``.
        """
        return str(self.options.get("client_id") or "")

    def is_configured(self) -> bool:
        """Whether the app credentials are set and the user authorized the app.

        Returns:
            bool: ``True`` if configured.
        """
        return bool(
            self.client_id
            and get_secret(self.id, "client_secret")
            and get_secret(self.id, "refresh_token")
        )

    def client(self, refresh_token: str | None = None) -> StravaClient:
        """Create an API client.

        Args:
            refresh_token (str | None): Token to use instead of the stored one.

        Raises:
            StravaError: If the Client ID or Secret is missing.

        Returns:
            StravaClient: A new client (close it after use).
        """
        secret = get_secret(self.id, "client_secret")
        if not self.client_id or not secret:
            raise StravaError("Enter the Strava Client ID and Client Secret first")
        return StravaClient(
            self.client_id, secret, refresh_token or get_secret(self.id, "refresh_token")
        )

    def _keep_token(self, client: StravaClient) -> None:
        """Store the refresh token again if Strava rotated it.

        Args:
            client (StravaClient): The client used.
        """
        token = client.refresh_token
        if token and token != get_secret(self.id, "refresh_token"):
            set_secret(self.id, "refresh_token", token)

    async def authorize(self, code: str) -> str:
        """Complete the OAuth flow with the code from the callback.

        Args:
            code (str): The authorization code.

        Returns:
            str: The connected athlete's name.
        """
        async with self.client() as c:
            data = await c.exchange_code(code)
            self._keep_token(c)
        name = athlete_name(data.get("athlete") or {})
        self.options["athlete"] = name
        self.settings.save()
        return name

    def disconnect(self) -> None:
        """Forget the authorization (the app credentials are kept)."""
        delete_secret(self.id, "refresh_token")
        self.options.pop("athlete", None)
        self.settings.save()

    async def test(self) -> str:
        """Check the authorization by fetching the athlete.

        Returns:
            str: The athlete name.
        """
        async with self.client() as c:
            try:
                return athlete_name(await c.athlete())
            finally:
                self._keep_token(c)

    async def upload_activity(self, ride: RideUpload) -> str:
        """Upload a ride.

        Args:
            ride (RideUpload): The ride to upload.

        Returns:
            str: Id of the created activity.
        """
        async with self.client() as c:
            try:
                return await c.upload_activity(ride)
            finally:
                self._keep_token(c)
