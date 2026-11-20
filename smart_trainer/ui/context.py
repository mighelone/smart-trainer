"""The objects the pages are built with."""

from __future__ import annotations

from dataclasses import dataclass, field

from smart_trainer.config import Settings
from smart_trainer.core.engine import Engine
from smart_trainer.ui.window import MiniWindow


@dataclass(kw_only=True, eq=False)
class AppContext:
    """The objects the pages are built with, wired once by ``main.run``.

    Attributes:
        engine (Engine): The application engine.
        settings (Settings): User settings (the same object the engine uses).
        window (MiniWindow): Full app / mini overlay switcher.
        fake (bool): Whether the app runs with simulated devices.
    """

    engine: Engine
    settings: Settings
    window: MiniWindow = field(default_factory=MiniWindow)
    fake: bool = False
