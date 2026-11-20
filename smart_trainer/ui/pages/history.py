"""History page: recorded sessions and manual upload."""

from __future__ import annotations

import asyncio
from collections import deque

from nicegui import ui

from smart_trainer.core.recorder import SessionInfo, UploadState
from smart_trainer.core.sessions import SessionStore
from smart_trainer.sync.base import Capability, Connection
from smart_trainer.sync.service import SyncService
from smart_trainer.ui.widgets.text import fmt_time


def upload_chip(name: str, state: UploadState | None) -> None:
    """Show the upload state of a session on one connection.

    Args:
        name (str): Connection name.
        state (UploadState | None): Upload outcome, ``None`` if never uploaded.
    """
    if state and state.done:
        icon, color, tip = "cloud_done", "positive", f"Uploaded to {name} ({state.remote_id})"
    elif state and state.error:
        icon, color, tip = "error", "negative", f"{name} upload failed: {state.error}"
    else:
        icon, color, tip = "cloud_off", "grey", f"Not uploaded to {name}"
    with ui.row().classes("items-center gap-1 no-wrap"):
        ui.icon(icon, color=color, size="xs")
        ui.label(name).classes("list-sub")
        ui.tooltip(tip)


def session_icon(session: SessionInfo) -> tuple[str, str]:
    """Icon and color for a session's status.

    Args:
        session (SessionInfo): The session.

    Returns:
        tuple[str, str]: Material icon name and color.
    """
    if session.status == "finished":
        done = any(u.done for u in session.uploads.values())
        return ("cloud_done", "positive") if done else ("save", "primary")
    if session.status == "active":
        return "warning", "warning"
    return "help", "grey"


class HistoryPage:
    """History page widgets: recorded sessions and manual upload.

    Attributes:
        store (SessionStore): Recorded sessions.
        sync (SyncService): Connections that upload rides.
        messages (deque[str]): Status messages from the core, shown after an upload.
    """

    PAGE_SIZE = 30

    def __init__(self, store: SessionStore, sync: SyncService, messages: deque[str]) -> None:
        """Build the page and schedule loading the sessions.

        Args:
            store (SessionStore): Recorded sessions.
            sync (SyncService): Connections that upload rides.
            messages (deque[str]): Status messages from the core.
        """
        self.store = store
        self.sync = sync
        self.messages = messages
        self.limit = self.PAGE_SIZE
        with ui.column().classes("page page-narrow"):
            ui.label("Sessions").classes("section-title")
            ui.label(f"Stored in {store.directory}").classes("hint")
            self.listing = ui.column().classes("w-full gap-2")
            self.more = (
                ui.button("Load more", icon="expand_more", on_click=self.load_more)
                .props("flat color=secondary")
                .mark("history-more")
            )
            self.more.set_visibility(False)
        ui.timer(0, self.render, once=True)

    async def render(self) -> None:
        """Redraw the list of recorded sessions (read in a worker thread)."""
        # one extra to know whether there's more to load
        sessions = await asyncio.to_thread(self.store.list_sessions, self.limit + 1)
        self.listing.clear()
        uploaders = self.sync.with_capability(Capability.UPLOAD)
        with self.listing:
            if not sessions:
                ui.label("No sessions recorded yet.").classes("hint")
            for session in sessions[: self.limit]:
                self._session_card(session, uploaders)
        self.more.set_visibility(len(sessions) > self.limit)

    async def load_more(self) -> None:
        """Show the next page of older sessions."""
        self.limit += self.PAGE_SIZE
        await self.render()

    def _session_card(self, session: SessionInfo, uploaders: list[Connection]) -> None:
        """Render one session with its upload state and, if needed, an upload button.

        Args:
            session (SessionInfo): The session.
            uploaders (list[Connection]): Connections that can upload rides.
        """
        names = {c.id: c.name for c in uploaders}
        with ui.card().classes("w-full panel"), ui.row().classes("items-center w-full"):
            icon, color = session_icon(session)
            ui.icon(icon, color=color, size="sm")
            with ui.column().classes("gap-0"):
                ui.label(session.name).classes("list-title")
                when = f"{session.started:%a %d %b %Y %H:%M}"
                ui.label(f"{when} · {fmt_time(session.elapsed_s)} · {session.status}").classes(
                    "list-sub"
                )
                shown = [c.id for c in uploaders if c.usable or c.id in session.uploads]
                shown += [cid for cid in session.uploads if cid not in shown]
                if session.status == "finished" and shown:
                    with ui.row().classes("gap-3"):
                        for cid in shown:
                            upload_chip(names.get(cid, cid), session.uploads.get(cid))
            ui.space()
            pending = [c.name for c in uploaders if c.usable and not session.uploaded(c.id)]
            if (
                session.status == "finished"
                and session.path.with_suffix(".fit").exists()
                and pending
            ):
                ui.button(
                    "Upload", icon="cloud_upload", on_click=lambda: self.upload(session)
                ).props("color=secondary").tooltip("Upload to " + ", ".join(pending))

    async def upload(self, session: SessionInfo) -> None:
        """Upload a finished session where it's still missing.

        Args:
            session (SessionInfo): The session to upload.
        """
        seen = len(self.messages)
        result = await self.store.upload_session(session)
        ok = bool(result) and all(result.values())
        for message in list(self.messages)[seen:]:
            ui.notify(message, type="positive" if ok else "negative")
        await self.render()
