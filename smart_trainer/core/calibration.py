"""Device calibration (trainer spin-down, power meter zero offset)."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable, Mapping

from smart_trainer.ble.device import (
    Calibratable,
    CalibrationError,
    CalibrationPhase,
    CalibrationProgress,
    CalibrationResult,
    Device,
)
from smart_trainer.core.control import TrainerController
from smart_trainer.core.state import ConnState

log = logging.getLogger(__name__)


class CalibrationService:
    """Runs one calibration at a time and publishes its progress.

    Attributes:
        device_id (str | None): Id of the device being calibrated, if any.
        progress (CalibrationProgress | None): Latest progress of the last calibration.
    """

    def __init__(
        self,
        devices: Mapping[str, Device],
        controller: TrainerController,
        messages: deque[str],
        ride_active: Callable[[], bool],
    ) -> None:
        """Initialize the service.

        Args:
            devices (Mapping[str, Device]): Active devices by id.
            controller (TrainerController): Trainer control, resent after a calibration.
            messages (deque[str]): User-facing status messages.
            ride_active (Callable[[], bool]): Whether a ride is in progress.
        """
        self._devices = devices
        self._controller = controller
        self._messages = messages
        self._ride_active = ride_active
        self.device_id: str | None = None
        self.progress: CalibrationProgress | None = None

    def blocker(self, device_id: str) -> str | None:
        """Why a device can't be calibrated right now.

        Args:
            device_id (str): Id of the device to calibrate.

        Returns:
            str | None: A user-facing reason, or ``None`` if calibration can start.
        """
        dev = self._devices.get(device_id)
        if not isinstance(dev, Calibratable):
            return "This device does not support calibration."
        if self._ride_active():
            return "Finish the ride before calibrating."
        if self.device_id is not None:
            return "A calibration is already running."
        if dev.state != ConnState.CONNECTED:
            return "The device is not connected."
        return None

    def ride_blocker(self) -> str | None:
        """Why a ride can't start right now.

        Returns:
            str | None: A user-facing reason, or ``None`` if nothing is calibrating.
        """
        if self.device_id is not None:
            return "Wait for the calibration to finish before riding."
        return None

    def suspends_control(self, trainer_id: str | None) -> bool:
        """Whether trainer commands must be held back (the trainer is spinning down).

        Args:
            trainer_id (str | None): Id of the assigned trainer.

        Returns:
            bool: ``True`` while the trainer itself is calibrating.
        """
        return self.device_id is not None and self.device_id == trainer_id

    async def run(self, device_id: str) -> CalibrationResult:
        """Calibrate a device; progress is published in :attr:`progress`.

        Args:
            device_id (str): Id of the device to calibrate.

        Returns:
            CalibrationResult: The outcome.

        Raises:
            CalibrationError: If calibration is not possible now or fails.
        """
        reason = self.blocker(device_id)
        if reason:
            raise CalibrationError(reason)
        dev = self._devices[device_id]
        assert isinstance(dev, Calibratable)

        def on_progress(progress: CalibrationProgress) -> None:
            """Publish a progress update.

            Args:
                progress (CalibrationProgress): The update.
            """
            self.progress = progress

        self.device_id = device_id
        self.progress = CalibrationProgress(CalibrationPhase.STARTING, "Starting…")
        try:
            result = await dev.calibrate(on_progress)
        except CalibrationError as exc:
            self.progress = CalibrationProgress(CalibrationPhase.FAILED, str(exc))
            self._messages.append(f"{dev.name}: calibration failed")
            raise
        except asyncio.CancelledError:
            self.progress = CalibrationProgress(CalibrationPhase.FAILED, "Calibration cancelled.")
            raise
        except Exception as exc:
            log.exception("%s: calibration failed", dev.name)
            msg = f"Calibration failed: {exc or type(exc).__name__}"
            self.progress = CalibrationProgress(CalibrationPhase.FAILED, msg)
            raise CalibrationError(msg) from exc
        finally:
            self.device_id = None
            self._controller.force_resend()
        self.progress = CalibrationProgress(CalibrationPhase.DONE, result.message, fraction=1.0)
        self._messages.append(f"{dev.name}: {result.message}")
        return result
