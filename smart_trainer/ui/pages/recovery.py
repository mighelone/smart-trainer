"""Crash recovery: resume, finish or discard a session the app left unfinished."""

from __future__ import annotations

from nicegui import ui

from smart_trainer.core.recorder import SessionInfo
from smart_trainer.core.ride import Ride, RideState
from smart_trainer.core.sessions import SessionStore
from smart_trainer.ui.widgets.text import fmt_time


class RecoveryDialog:
    """Offers to resume, finish or discard a session left unfinished by a crash."""

    def __init__(self, ride: Ride, store: SessionStore, info: SessionInfo) -> None:
        """Build and open the dialog.

        Args:
            ride (Ride): The ride (to resume into).
            store (SessionStore): Recorded sessions (to finish or discard).
            info (SessionInfo): The unfinished session.
        """
        self.ride = ride
        self.store = store
        self.info = info
        with ui.dialog().props("persistent") as self.dialog, ui.card():
            ui.label("Unfinished session found").classes("text-xl font-bold")
            ui.label(
                f"{info.name}, started {info.started:%a %d %b %H:%M}, "
                f"{fmt_time(info.elapsed_s)} recorded ({info.n_samples} samples)"
            )
            ui.label("The app was closed or crashed during this ride.").classes("opacity-70")
            with ui.row():
                ui.button("Resume", icon="play_arrow", on_click=self.resume).props("color=positive")
                ui.button("Finish & save", icon="save", on_click=self.finish)
                ui.button("Discard", icon="delete", on_click=self.discard).props(
                    "flat color=negative"
                )
        self.dialog.open()

    @classmethod
    def offer(cls, ride: Ride, store: SessionStore) -> RecoveryDialog | None:
        """Open the dialog if no ride is in progress and a session was left unfinished.

        Args:
            ride (Ride): The ride.
            store (SessionStore): Recorded sessions.

        Returns:
            RecoveryDialog | None: The dialog, or ``None`` when there is nothing to recover.
        """
        if ride.state != RideState.IDLE:
            return None
        crashed = ride.recoverable_sessions()
        return cls(ride, store, crashed[0]) if crashed else None

    def resume(self) -> None:
        """Resume the unfinished session."""
        try:
            self.ride.resume(self.info)
        except RuntimeError as exc:
            ui.notify(str(exc), type="warning")
            return
        self.dialog.close()
        ui.notify("Session resumed: start pedalling")

    async def finish(self) -> None:
        """Finalize the unfinished session without resuming it."""
        self.dialog.close()
        fit = await self.store.finish_crashed(self.info)
        ui.notify(f"Saved {fit.name if fit else 'session'}")

    def discard(self) -> None:
        """Move the unfinished session to the discarded folder."""
        self.dialog.close()
        self.store.discard(self.info)
        ui.notify("Session moved to sessions/discarded")
