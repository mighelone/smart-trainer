"""The objects the pages are built with."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from smart_trainer.config import Settings
from smart_trainer.core.control import TrainerController
from smart_trainer.core.devices import DeviceHub
from smart_trainer.core.engine import Engine
from smart_trainer.core.ride import Ride
from smart_trainer.core.state import Snapshot
from smart_trainer.ui.strava_auth import StravaAuthorization
from smart_trainer.ui.window import MiniWindow


@dataclass(kw_only=True, eq=False)
class AppContext:
    """The objects the pages are built with, wired once by ``main.run``.

    Attributes:
        engine (Engine): The application engine.
        settings (Settings): User settings (the same object the engine uses).
        strava (StravaAuthorization): Strava OAuth flow (pending requests, redirect route).
        window (MiniWindow): Full app / mini overlay switcher.
        fake (bool): Whether the app runs with simulated devices.
    """

    engine: Engine
    settings: Settings
    strava: StravaAuthorization
    window: MiniWindow = field(default_factory=MiniWindow)
    fake: bool = False


@dataclass(frozen=True, kw_only=True)
class LiveRide:
    """What the live screens (ride page, mini overlay) read and control.

    Attributes:
        ride (Ride): The ride in progress.
        controller (TrainerController): Trainer control (mode, target, last command).
        devices (DeviceHub): Devices and their roles.
        snapshot (Callable[[], Snapshot]): Latest metrics; a function because the engine
            publishes a new snapshot every tick.
    """

    ride: Ride
    controller: TrainerController
    devices: DeviceHub
    snapshot: Callable[[], Snapshot]

    @classmethod
    def of(cls, engine: Engine) -> LiveRide:
        """Take the live screens' part of the engine.

        Args:
            engine (Engine): The application engine.

        Returns:
            LiveRide: The ride, controller, devices and a snapshot reader.
        """
        return cls(
            ride=engine.ride,
            controller=engine.controller,
            devices=engine.devices,
            snapshot=lambda: engine.snapshot,
        )
