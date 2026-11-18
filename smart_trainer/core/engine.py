"""The app core: wires the components together and runs the 1 Hz loop.

Each concern lives in its own component, exposed as an attribute:

- :attr:`Engine.devices` (:class:`DeviceHub`): devices and role assignment;
- :attr:`Engine.ride` (:class:`Ride`): the ride in progress;
- :attr:`Engine.calibration` (:class:`CalibrationService`): device calibration;
- :attr:`Engine.store` (:class:`SessionStore`): session files, FIT export, recovery;
- :attr:`Engine.sync` (:class:`SyncService`): uploads and profile sync.

The UI only reads component state and calls component methods, so the core is
UI-agnostic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from smart_trainer.config import Settings, sessions_dir
from smart_trainer.core.calibration import CalibrationService
from smart_trainer.core.control import TrainerController
from smart_trainer.core.devices import DeviceHub
from smart_trainer.core.ride import Ride
from smart_trainer.core.sessions import SessionStore
from smart_trainer.core.sources import SourceSelector
from smart_trainer.core.state import Snapshot
from smart_trainer.sync.service import ProviderFactory, SyncService

log = logging.getLogger(__name__)


@dataclass(kw_only=True, eq=False)
class Engine:
    """Composition root of the app core, plus the control loop.

    The components are injected; use :meth:`from_settings` to build the default graph.
    ``eq=False`` keeps identity semantics: an engine is a service, not a value.

    Attributes:
        messages (deque[str]): User-facing status messages, shared by the components.
        selector (SourceSelector): Resolves readings into snapshots.
        controller (TrainerController): Trainer control logic.
        devices (DeviceHub): Active devices and their roles.
        sync (SyncService): Training-platform sync.
        store (SessionStore): Recorded sessions.
        calibration (CalibrationService): Device calibration.
        ride (Ride): The ride in progress.
        tick_s (float): Loop interval in seconds.
        snapshot (Snapshot): Latest effective metrics.
    """

    messages: deque[str]
    selector: SourceSelector
    controller: TrainerController
    devices: DeviceHub
    sync: SyncService
    store: SessionStore
    calibration: CalibrationService
    ride: Ride
    tick_s: float = 1.0
    snapshot: Snapshot = field(default_factory=Snapshot)
    _task: asyncio.Task | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        sessions: Path | None = None,
        tick_s: float = 1.0,
        sync_provider: ProviderFactory | None = None,
    ) -> Engine:
        """Build the default component graph and the engine around it.

        Args:
            settings (Settings): User settings.
            sessions (Path | None): Session directory; defaults to :func:`sessions_dir`.
            tick_s (float): Loop interval in seconds.
            sync_provider (ProviderFactory | None): Creates the sync provider; without
                one, uploads and profile sync are unavailable.

        Returns:
            Engine: A ready engine (call :meth:`start` to run the loop).
        """
        messages: deque[str] = deque(maxlen=20)
        selector = SourceSelector()
        controller = TrainerController(settings.power_match)
        devices = DeviceHub(settings, selector, controller)

        # Sync and calibration need to know whether a ride is active, and the ride needs
        # the calibration's blocker: the closure binds ``ride`` once it exists below.
        def ride_active() -> bool:
            """Whether a ride is in progress.

            Returns:
                bool: ``True`` while riding or finishing.
            """
            return ride.active

        sync = SyncService(settings, messages, sync_provider or (lambda: None), ride_active)
        store = SessionStore(sessions or sessions_dir(), settings, sync, messages)
        calibration = CalibrationService(devices, controller, messages, ride_active)
        ride = Ride(settings, controller, store, messages, tick_s, blocker=calibration.ride_blocker)
        return cls(
            messages=messages,
            selector=selector,
            controller=controller,
            devices=devices,
            sync=sync,
            store=store,
            calibration=calibration,
            ride=ride,
            tick_s=tick_s,
        )

    # --------------------------------------------------------------------- loop
    def start(self) -> None:
        """Start the engine loop if not already running."""
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="engine")

    async def shutdown(self) -> None:
        """Stop the loop and all devices, leaving any active session recoverable."""
        if self._task:
            self._task.cancel()
        await self.devices.stop_all()
        self.ride.close()

    async def _loop(self) -> None:
        """Run :meth:`tick` every ``tick_s`` seconds until cancelled."""
        next_t = time.monotonic()
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("engine tick failed")
            next_t += self.tick_s
            await asyncio.sleep(max(0.0, next_t - time.monotonic()))

    async def tick(self, now_wall: float | None = None) -> None:
        """Run one engine step: snapshot, ride progress, then trainer control.

        Args:
            now_wall (float | None): Current unix time; defaults to ``time.time()``.
        """
        now_wall = now_wall or time.time()
        snap = self.snapshot = self.selector.snapshot()
        self.ride.tick(snap, now_wall)
        if self.calibration.suspends_control(self.devices.trainer_id):
            return  # never send resistance commands while the trainer spins down
        await self.controller.apply(
            snap,
            self.selector.avg_power(self.devices.power_id),
            self.selector.avg_power(self.devices.trainer_id),
            self.devices.trainer_connected(),
            now_wall,
        )
