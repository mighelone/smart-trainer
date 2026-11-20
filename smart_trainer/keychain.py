"""Connection secrets (API keys, tokens): the environment first, then the system keyring."""

from __future__ import annotations

import contextlib
import logging
import os
import re

log = logging.getLogger(__name__)

KEYRING_SERVICE = "smart-trainer"
KEYRING_INTERVALS = "intervals.icu"


def _secret_env(conn_id: str, name: str) -> str:
    """Environment variable that overrides a secret, e.g. ``SMART_TRAINER_STRAVA_CLIENT_SECRET``."""
    return re.sub(r"[^A-Z0-9]+", "_", f"SMART_TRAINER_{conn_id}_{name}".upper())


def get_secret(conn_id: str, name: str) -> str | None:
    """Get a connection secret from the environment or the system keyring.

    Args:
        conn_id (str): Connection id (e.g. ``"strava"``).
        name (str): Secret name (e.g. ``"refresh_token"``).

    Returns:
        str | None: The secret, or ``None`` if not set.
    """
    if value := os.environ.get(_secret_env(conn_id, name)):
        return value
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, f"{conn_id}.{name}")
    except Exception:
        log.exception("keyring unavailable")
        return None


def set_secret(conn_id: str, name: str, value: str) -> None:
    """Store a connection secret in the system keyring.

    Args:
        conn_id (str): Connection id.
        name (str): Secret name.
        value (str): Secret value.
    """
    import keyring

    keyring.set_password(KEYRING_SERVICE, f"{conn_id}.{name}", value)


def delete_secret(conn_id: str, name: str) -> None:
    """Remove a connection secret from the system keyring (no error if missing).

    Args:
        conn_id (str): Connection id.
        name (str): Secret name.
    """
    import keyring
    from keyring.errors import PasswordDeleteError

    with contextlib.suppress(PasswordDeleteError):
        keyring.delete_password(KEYRING_SERVICE, f"{conn_id}.{name}")


def get_api_key() -> str | None:
    """Get the intervals.icu API key.

    Looks at the ``INTERVALS_API_KEY`` environment variable first, then the
    system keyring.

    Returns:
        str | None: The API key, or ``None`` if not configured.
    """
    if key := os.environ.get("INTERVALS_API_KEY"):
        return key
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, KEYRING_INTERVALS)
    except Exception:
        log.exception("keyring unavailable")
        return None


def set_api_key(key: str) -> None:
    """Store the intervals.icu API key in the system keyring.

    Args:
        key (str): The API key.
    """
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_INTERVALS, key)
