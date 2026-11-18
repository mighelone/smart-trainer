"""Lookup of the available connections. Built-in connections register on import.

Filtering by capability and state lives in :class:`smart_trainer.sync.service.SyncService`.
"""

from __future__ import annotations

from smart_trainer.config import Settings
from smart_trainer.sync import intervals, strava  # noqa: F401  (register the built-ins)
from smart_trainer.sync.base import REGISTRY, Connection


def all_connections(settings: Settings) -> list[Connection]:
    """Instantiate every registered connection.

    Args:
        settings (Settings): Application settings.

    Returns:
        list[Connection]: All connections, in registration order.
    """
    return [cls(settings) for cls in REGISTRY.values()]


def get(settings: Settings, conn_id: str) -> Connection | None:
    """Instantiate one connection by id.

    Args:
        settings (Settings): Application settings.
        conn_id (str): Connection id.

    Returns:
        Connection | None: The connection, or ``None`` if unknown.
    """
    cls = REGISTRY.get(conn_id)
    return cls(settings) if cls else None
