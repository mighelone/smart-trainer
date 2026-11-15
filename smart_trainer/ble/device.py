"""Device driver base class + registry.

A driver declares the BLE service it handles and the roles it can provide. The core
only talks to roles, so supporting a new device = adding one driver module.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Callable, ClassVar

from smart_trainer.core.state import ConnState, Reading, Role

log = logging.getLogger(__name__)

ReadingCallback = Callable[[Reading], None]


class Device(ABC):
    SERVICE_UUID: ClassVar[str] = ""
    ROLES: ClassVar[frozenset[Role]] = frozenset()
    PRIORITY: ClassVar[int] = 0  # higher wins when a device matches several drivers
    DRIVER: ClassVar[str] = ""

    def __init__(self, address: str, name: str = "") -> None:
        self.address = address
        self.name = name or address
        self.state = ConnState.DISCONNECTED
        self._callbacks: list[ReadingCallback] = []
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def id(self) -> str:
        return self.address

    def subscribe(self, cb: ReadingCallback) -> None:
        self._callbacks.append(cb)

    def emit(self, reading: Reading) -> None:
        for cb in self._callbacks:
            try:
                cb(reading)
            except Exception:  # never let a UI/consumer bug kill the BLE task
                log.exception("reading callback failed")

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._supervise(), name=f"dev-{self.name}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self.state = ConnState.DISCONNECTED

    async def _supervise(self) -> None:
        """Connect, run until disconnected, reconnect with exponential backoff."""
        backoff = 1.0
        while not self._stop.is_set():
            self.state = ConnState.CONNECTING
            try:
                await self.run_connected()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("%s: connection error: %s", self.name, exc)
            self.state = ConnState.DISCONNECTED
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 15.0)

    @abstractmethod
    async def run_connected(self) -> None:
        """Connect and block until the connection is lost. Set self.state = CONNECTED once up."""


class ControllableTrainer(ABC):
    """Interface for devices that can apply resistance."""

    @abstractmethod
    async def set_erg(self, watts: float) -> None: ...

    @abstractmethod
    async def set_resistance(self, pct: float) -> None: ...

    @abstractmethod
    async def set_slope(self, grade_pct: float) -> None: ...


DRIVERS: dict[str, type[Device]] = {}


def register(cls: type[Device]) -> type[Device]:
    DRIVERS[cls.DRIVER] = cls
    return cls


def drivers_for_services(service_uuids: list[str]) -> list[type[Device]]:
    uuids = {u.lower() for u in service_uuids}
    found = [d for d in DRIVERS.values() if d.SERVICE_UUID and d.SERVICE_UUID in uuids]
    return sorted(found, key=lambda d: -d.PRIORITY)
