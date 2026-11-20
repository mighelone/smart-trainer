"""The app core: wires the components together and runs the 1 Hz loop.

Each concern lives in its own component, exposed as an attribute:

- :attr:`Engine.devices` (:class:`DeviceHub`): devices and role assignment;
- :attr:`Engine.ride` (:class:`Ride`): the ride in progress;
- :attr:`Engine.calibration` (:class:`CalibrationService`): device calibration;
- :attr:`Engine.store` (:class:`SessionStore`): session files, FIT export, recovery;
- :attr:`Engine.sync` (:class:`SyncService`): uploads and profile sync, through the
  connections (intervals.icu, Strava, …).

The UI only reads component state and calls component methods, so the core is
UI-agnostic. The engine is an async context manager that owns the lifecycle::

    async with Engine.from_settings(settings) as engine:
        ...  # the loop runs; on exit the loop, devices and recorder are stopped
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Self

from smart_trainer.config import Settings, sessions_dir
from smart_trainer.core.calibration import CalibrationService
from smart_trainer.core.control import SpiralGuard, TrainerController
from smart_trainer.core.devices import DeviceHub
from smart_trainer.core.ride import MAX_TICK_DT_S, Ride
from smart_trainer.core.sessions import SessionStore
from smart_trainer.core.sources import SourceSelector
from smart_trainer.core.state import Snapshot
from smart_trainer.sync.service import ConnectionsFactory, SyncService

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
        run_loop (bool): Whether entering the context starts the 1 Hz loop. Turn it off
            to drive :meth:`tick` by hand (e.g. in tests with simulated time).
        snapshot (Snapshot): Latest effective metrics, published by :meth:`tick`
            (the values used for control and recording; read-only for consumers).
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
    run_loop: bool = True
    snapshot: Snapshot = field(default_factory=Snapshot, init=False)
    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _last_tick_mono: float | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        sessions: Path | None = None,
        tick_s: float = 1.0,
        connections: ConnectionsFactory | None = None,
        run_loop: bool = True,
    ) -> Engine:
        """Build the default component graph and the engine around it.

        Args:
            settings (Settings): User settings.
            sessions (Path | None): Session directory; defaults to :func:`sessions_dir`.
            tick_s (float): Loop interval in seconds.
            connections (ConnectionsFactory | None): Returns the connections (e.g.
                :func:`smart_trainer.sync.registry.all_connections`); without it, uploads
                and profile sync are unavailable.
            run_loop (bool): Whether entering the context starts the 1 Hz loop.

        Returns:
            Engine: A ready engine; enter it with ``async with`` to run it.
        """
        messages: deque[str] = deque(maxlen=20)
        selector = SourceSelector()
        guard = SpiralGuard() if settings.erg_spiral_guard else None
        controller = TrainerController(settings.power_match, guard)
        devices = DeviceHub(
            settings, on_reading=selector.on_reading, on_change=controller.configure
        )

        # Sync and calibration need to know whether a ride is active, and the ride needs
        # the calibration's blocker: the closure binds ``ride`` once it exists below.
        def ride_active() -> bool:
            """Whether a ride is in progress.

            Returns:
                bool: ``True`` while riding or finishing.
            """
            return ride.active

        sync = SyncService(settings, messages, connections or (lambda: []), ride_active)
        store = SessionStore(sessions or sessions_dir(), sync, messages)
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
            run_loop=run_loop,
        )

    # ---------------------------------------------------------------- lifecycle
    async def __aenter__(self) -> Self:
        """Start the loop (unless ``run_loop`` is off).

        Returns:
            Self: The engine.

        Raises:
            RuntimeError: If the engine is already running.
        """
        if self._task is not None:
            raise RuntimeError("Engine is already running")
        if self.run_loop:
            self._task = asyncio.create_task(self._loop(), name="engine")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop the loop and all devices, leaving any active session recoverable.

        Args:
            exc_type (type[BaseException] | None): Exception type, if the block raised.
            exc (BaseException | None): The exception, if the block raised.
            tb (TracebackType | None): Its traceback.
        """
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.devices.stop_all()
        self.ride.close()  # not finished: the session stays 'active', hence recoverable

    # --------------------------------------------------------------------- loop

    async def _loop(self) -> None:
        """Run :meth:`tick` every ``tick_s`` seconds until cancelled."""
        next_t = time.monotonic()
        while True:
            started = time.monotonic()
            try:
                await self.tick()
            except Exception:
                log.exception("engine tick failed")
            next_t += self.tick_s
            delay = next_t - time.monotonic()
            if delay <= 0:
                log.warning(
                    "engine tick overran: took %.3f s, %.3f s behind schedule",
                    time.monotonic() - started,
                    -delay,
                )
            await asyncio.sleep(max(0.0, delay))

    async def tick(self, now_wall: float | None = None, now_mono: float | None = None) -> None:
        """Run one engine step: snapshot, ride progress, then trainer control.

        The ride advances by the monotonic time since the previous tick (clamped to
        ``0..MAX_TICK_DT_S``), so a late tick doesn't make the workout clock drift.

        Args:
            now_wall (float | None): Current unix time, used for recording;
                defaults to ``time.time()``.
            now_mono (float | None): Current monotonic time, used for timing (ride
                progress, trainer command resend, power matching); defaults to
                ``time.monotonic()``.
        """
        now_wall = now_wall if now_wall is not None else time.time()
        now_mono = now_mono if now_mono is not None else time.monotonic()
        dt = self._elapsed_since_last_tick(now_mono)
        assignment = self.devices.assignment
        snapshot = self.snapshot = self.selector.create_snapshot(assignment)
        self.ride.tick(snapshot, now_wall, dt)
        if self.calibration.suspends_control(assignment.trainer_id):
            return  # never send resistance commands while the trainer spins down
        await self.controller.apply(snapshot, self.devices.trainer_connected(), now_mono)

    def _elapsed_since_last_tick(self, now_mono: float) -> float:
        """Measure the time since the previous tick and remember this one.

        Args:
            now_mono (float): Current monotonic time.

        Returns:
            float: Seconds since the previous tick, clamped to ``0..MAX_TICK_DT_S``
            (``tick_s`` on the first tick).
        """
        last, self._last_tick_mono = self._last_tick_mono, now_mono
        if last is None:
            return self.tick_s
        return min(max(now_mono - last, 0.0), MAX_TICK_DT_S)
