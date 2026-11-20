"""Native window modes: the full app and the always-on-top mini overlay."""

from __future__ import annotations

from typing import Protocol, cast

from nicegui import app, ui

MINI_SIZE = (1000, 210)  # native window size of the mini overlay (incl. title bar)
FULL_SIZE = (1280, 860)  # size restored when the app was started in mini mode


class NativeWindow(Protocol):
    """The part of NiceGUI's native ``WindowProxy`` used here.

    NiceGUI falls back to an empty ``WindowProxy`` class when pywebview is missing, which hides
    these methods from type checkers.
    """

    async def get_size(self) -> tuple[int, int]: ...
    async def get_position(self) -> tuple[int, int]: ...
    def set_always_on_top(self, on_top: bool) -> None: ...
    def resize(self, width: int, height: int) -> None: ...
    def move(self, x: int, y: int) -> None: ...


def native_window() -> NativeWindow | None:
    """The native desktop window, or ``None`` when running in a browser.

    Returns:
        NativeWindow | None: The window proxy.
    """
    return cast("NativeWindow | None", app.native.main_window)


class MiniWindow:
    """Switches between the full app and the mini overlay, and remembers the full window.

    Attributes:
        start_mini (bool): Open the mini overlay on the first visit (``--mini``).
    """

    def __init__(self, start_mini: bool = False) -> None:
        """Create the switcher.

        Args:
            start_mini (bool): Open the mini overlay on the first visit.
        """
        self.start_mini = start_mini
        self._full_geometry: tuple[tuple[int, int], tuple[int, int]] | None = None

    def take_start_request(self) -> bool:
        """Whether to open the mini overlay now; answers ``True`` only once.

        Returns:
            bool: ``True`` on the first call after starting with ``--mini``.
        """
        start, self.start_mini = self.start_mini, False
        return start

    async def enter(self) -> None:
        """Switch to the mini overlay: small, always-on-top window (native) and /mini."""
        w = native_window()
        if w is not None:
            self._full_geometry = (await w.get_size(), await w.get_position())
            w.set_always_on_top(True)
            w.resize(*MINI_SIZE)
        ui.navigate.to("/mini")

    def restore_full(self) -> None:
        """Restore the normal window: not on top, previous size and position (native only)."""
        w = native_window()
        if w is None:
            return
        w.set_always_on_top(False)
        if self._full_geometry:
            (width, height), (x, y) = self._full_geometry
            w.resize(width, height)
            w.move(x, y)
        else:
            w.resize(*FULL_SIZE)

    def exit(self) -> None:
        """Leave the mini overlay and go back to the full ride page."""
        self.restore_full()
        ui.navigate.to("/")
