"""Device driver base class + registry.

A driver declares the BLE service it handles and the roles it can provide. The core
only talks to roles, so supporting a new device = adding one driver module.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from smart_trainer.ble.parsers.zwift import Button
from smart_trainer.core.state import ConnState, Reading, Role

log = logging.getLogger(__name__)

ReadingCallback = Callable[[Reading], None]
ShiftCallback = Callable[[int], None]
"""Called with the gears to shift: ``1`` one gear harder, ``-1`` one gear easier."""


class DriverId(StrEnum):
    """Unique identifier of a device driver, persisted in the user settings."""

    FTMS = "ftms"
    CYCLING_POWER = "cycling_power"
    HEART_RATE = "heart_rate"
    FAKE_TRAINER = "fake_trainer"
    FAKE_POWER = "fake_power"
    FAKE_HR = "fake_hr"
    ZWIFT_CLICK = "zwift_click"
    FAKE_CLICK = "fake_click"


class Device(ABC):
    """Abstract base class for all device drivers.

    Handles reading subscriptions and a supervised connect/reconnect loop.

    Attributes:
        SERVICE_UUID (str): BLE service UUID this driver handles.
        ROLES (frozenset[Role]): Data roles the device can provide.
        PRIORITY (int): Higher wins when a device matches several drivers.
        DRIVER (DriverId): Unique driver identifier used in the registry.
        address (str): Device address.
        name (str): Human-readable device name.
        state (ConnState): Current connection state.
    """

    SERVICE_UUID: ClassVar[str] = ""
    ROLES: ClassVar[frozenset[Role]] = frozenset()
    PRIORITY: ClassVar[int] = 0  # higher wins when a device matches several drivers
    DRIVER: ClassVar[DriverId]

    def __init__(self, address: str, name: str | None = None) -> None:
        """Initialize the device.

        Args:
            address (str|None): BLE address (or platform UUID on macOS) of the device.
            name (str): Human-readable device name; defaults to the address.
        """
        self.address = address
        self.name = name or address
        self.state = ConnState.DISCONNECTED
        self._callbacks: list[ReadingCallback] = []
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def id(self) -> str:
        """Unique identifier of the device.

        Returns:
            str: The device address.
        """
        return self.address

    def subscribe(self, cb: ReadingCallback) -> None:
        """Register a callback to receive readings from this device.

        Args:
            cb (ReadingCallback): Function called with every emitted reading.
        """
        self._callbacks.append(cb)

    def emit(self, reading: Reading) -> None:
        """Dispatch a reading to all subscribed callbacks.

        Exceptions raised by callbacks are logged and swallowed so they never
        kill the BLE task.

        Args:
            reading (Reading): The reading to dispatch.
        """
        for cb in self._callbacks:
            try:
                cb(reading)
            except Exception:  # never let a UI/consumer bug kill the BLE task
                log.exception("reading callback failed")

    def start(self) -> None:
        """Start the supervision task if it is not already running."""
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._supervise(), name=f"dev-{self.name}")

    async def stop(self) -> None:
        """Stop the supervision task and mark the device as disconnected."""
        self._stop.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
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
        """Connect and block until the connection is lost.

        Implementations must set ``self.state = ConnState.CONNECTED`` once up.
        """


class ControllableTrainer(ABC):
    """Interface for devices that can apply resistance."""

    @abstractmethod
    async def set_erg(self, watts: float) -> None:
        """Set ERG mode with a target power.

        Args:
            watts (float): Target power in watts.
        """

    @abstractmethod
    async def set_resistance(self, pct: float) -> None:
        """Set a fixed resistance level.

        Args:
            pct (float): Resistance level as a percentage (0-100).
        """

    @abstractmethod
    async def set_slope(self, grade_pct: float) -> None:
        """Set simulation mode with a road grade.

        Args:
            grade_pct (float): Road grade in percent.
        """


class Shifter:
    """Mixin for devices with shift buttons: turns button states into shifts.

    A device calls :meth:`update_buttons` with every button state it receives. Only a
    press shifts (a released button becoming pressed); a held button, which controllers
    keep reporting, doesn't shift again.

    Attributes:
        SHIFT_STEPS (dict[Button, int]): Gears each button shifts.
    """

    SHIFT_STEPS: ClassVar[dict[Button, int]] = {Button.PLUS: 1, Button.MINUS: -1}

    def __init__(self) -> None:
        """Initialize with no button pressed and no subscriber."""
        self._pressed: frozenset[Button] = frozenset()
        self._shift_callbacks: list[ShiftCallback] = []

    def subscribe_shifts(self, cb: ShiftCallback) -> None:
        """Register a callback to receive the shifts.

        Args:
            cb (ShiftCallback): Called with the gears to shift on every press.
        """
        self._shift_callbacks.append(cb)

    def update_buttons(self, pressed: frozenset[Button]) -> None:
        """Take the latest button state and shift for each newly pressed button.

        Args:
            pressed (frozenset[Button]): The buttons pressed now.
        """
        for button in pressed - self._pressed:
            for cb in self._shift_callbacks:
                try:
                    cb(self.SHIFT_STEPS[button])
                except Exception:  # never let a consumer bug kill the BLE task
                    log.exception("shift callback failed")
        self._pressed = pressed


class CalibrationKind(StrEnum):
    """Kind of calibration a device supports."""

    SPIN_DOWN = "spin_down"  # smart trainer (FTMS spin-down)
    ZERO_OFFSET = "zero_offset"  # power meter (Cycling Power offset compensation)


class CalibrationPhase(StrEnum):
    """Step of a running calibration, used to guide the rider."""

    STARTING = "starting"
    SPEED_UP = "speed_up"  # pedal up to the target speed
    COAST = "coast"  # stop pedalling, let the flywheel spin down
    MEASURING = "measuring"  # keep still while the device measures
    DONE = "done"
    FAILED = "failed"


@dataclass(slots=True)
class CalibrationProgress:
    """Progress update emitted while a calibration runs.

    Attributes:
        phase (CalibrationPhase): Current step.
        message (str): User-facing instruction or status.
        speed_kmh (float | None): Current speed (spin-down only).
        target_low_kmh (float | None): Lower bound of the target speed (spin-down only).
        target_high_kmh (float | None): Upper bound of the target speed (spin-down only).
        fraction (float | None): Progress of the current phase in 0-1, if known.
    """

    phase: CalibrationPhase
    message: str
    speed_kmh: float | None = None
    target_low_kmh: float | None = None
    target_high_kmh: float | None = None
    fraction: float | None = None


@dataclass(slots=True)
class CalibrationResult:
    """Outcome of a successful calibration.

    Attributes:
        message (str): User-facing summary.
        offset (int | None): Raw zero offset reported by a power meter, if any.
    """

    message: str
    offset: int | None = None


class CalibrationError(Exception):
    """A calibration failed; the message is shown to the user."""


ProgressCallback = Callable[[CalibrationProgress], None]


class Calibratable(ABC):
    """Interface for devices that can be calibrated.

    Attributes:
        CALIBRATION (CalibrationKind): The calibration the device supports.
    """

    CALIBRATION: ClassVar[CalibrationKind]

    @abstractmethod
    async def calibrate(self, on_progress: ProgressCallback) -> CalibrationResult:
        """Run the calibration, reporting progress along the way.

        Args:
            on_progress (ProgressCallback): Called with every progress update.

        Returns:
            CalibrationResult: The outcome.

        Raises:
            CalibrationError: If the calibration fails or is refused.
        """


DRIVERS: dict[DriverId, type[Device]] = {}


def register(cls: type[Device]) -> type[Device]:
    """Class decorator that adds a driver to the :data:`DRIVERS` registry.

    Args:
        cls (type[Device]): The driver class to register.

    Returns:
        type[Device]: The same class, unchanged.
    """
    DRIVERS[cls.DRIVER] = cls
    return cls


def drivers_for_services(service_uuids: list[str]) -> list[type[Device]]:
    """Find the drivers able to handle any of the advertised services.

    Args:
        service_uuids (list[str]): Service UUIDs advertised by a device.

    Returns:
        list[type[Device]]: Matching driver classes, highest priority first.
    """
    uuids = {u.lower() for u in service_uuids}
    found = [d for d in DRIVERS.values() if d.SERVICE_UUID and d.SERVICE_UUID in uuids]
    return sorted(found, key=lambda d: -d.PRIORITY)
