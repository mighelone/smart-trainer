"""Simulated devices for development and tests without hardware.

A shared FakeWorld models the rider's *true* power. The fake trainer over-reads by
`trainer_bias` (default +4 %) so pedal power matching has something to correct.
"""

from __future__ import annotations

import asyncio
import random

from smart_trainer.ble.device import (
    Calibratable,
    CalibrationError,
    CalibrationKind,
    CalibrationPhase,
    CalibrationProgress,
    CalibrationResult,
    ControllableTrainer,
    Device,
    DriverId,
    ProgressCallback,
    register,
)
from smart_trainer.core.state import ConnState, Reading, Role


class FakeWorld:
    """Shared simulation state for the fake devices.

    Attributes:
        true_power (float): Rider's true power in watts.
        cadence (float): Current cadence in rpm.
        trainer_bias (float): Factor by which the fake trainer over-reads power.
        tick_s (float): Simulation tick interval in seconds.
        hr (float): Current heart rate in bpm.
        pedals_alive (bool): Whether the fake power meter emits readings.
        calibration_fails (bool): Whether simulated calibrations report an error.
    """

    def __init__(self, trainer_bias: float = 1.04, tick_s: float = 1.0) -> None:
        """Initialize the simulated world.

        Args:
            trainer_bias (float): Factor by which the fake trainer over-reads power.
            tick_s (float): Simulation tick interval in seconds.
        """
        self.true_power = 120.0
        self.cadence = 90.0
        self.trainer_bias = trainer_bias
        self.tick_s = tick_s
        self.hr = 95.0
        self.pedals_alive = True
        self.calibration_fails = False


WORLD = FakeWorld()


class _FakeBase(Device):
    """Base class for simulated devices driven by a :class:`FakeWorld`.

    Attributes:
        world (FakeWorld): The shared simulation state.
    """

    def __init__(self, address: str, name: str = "", world: FakeWorld | None = None) -> None:
        """Initialize the fake device.

        Args:
            address (str): Fake device address.
            name (str): Human-readable device name.
            world (FakeWorld | None): Simulation state; defaults to the global ``WORLD``.
        """
        super().__init__(address, name)
        self.world = world or WORLD

    async def run_connected(self) -> None:
        """Simulate a connection and call :meth:`step` every tick until stopped."""
        await asyncio.sleep(0.2)
        self.state = ConnState.CONNECTED
        while not self._stop.is_set():
            self.step()
            await asyncio.sleep(self.world.tick_s)

    def step(self) -> None:
        """Advance the simulation by one tick and emit a reading.

        Raises:
            NotImplementedError: Must be implemented by subclasses.
        """
        raise NotImplementedError


@register
class FakeTrainer(_FakeBase, ControllableTrainer, Calibratable):
    """Simulated controllable smart trainer with a simulated spin-down.

    Attributes:
        SPIN_DOWN_TARGET (tuple[float, float]): Target speed range in km/h.
        mode (str): Control mode, one of ``"erg"``, ``"resistance"`` or ``"slope"``.
        value (float): Target for the current mode (watts, percent or grade).
        calibration_speed (float | None): Simulated speed while spinning down, else ``None``.
    """

    DRIVER = DriverId.FAKE_TRAINER
    ROLES = frozenset({Role.TRAINER, Role.POWER, Role.CADENCE, Role.SPEED})
    CALIBRATION = CalibrationKind.SPIN_DOWN
    SPIN_DOWN_TARGET = (32.0, 36.0)

    def __init__(self, address: str = "fake-trainer", name: str = "Fake Trainer", world=None):
        """Initialize the fake trainer.

        Args:
            address (str): Fake device address.
            name (str): Human-readable device name.
            world (FakeWorld | None): Simulation state; defaults to the global ``WORLD``.
        """
        super().__init__(address, name, world)
        self.mode, self.value = "resistance", 30.0
        self.calibration_speed: float | None = None

    async def set_erg(self, watts: float) -> None:
        """Set ERG mode with a target power.

        Args:
            watts (float): Target power in watts.
        """
        self.mode, self.value = "erg", float(watts)

    async def set_resistance(self, pct: float) -> None:
        """Set a fixed resistance level.

        Args:
            pct (float): Resistance level as a percentage (0-100).
        """
        self.mode, self.value = "resistance", float(pct)

    async def set_slope(self, grade_pct: float) -> None:
        """Set simulation mode with a road grade.

        Args:
            grade_pct (float): Road grade in percent.
        """
        self.mode, self.value = "slope", float(grade_pct)

    async def calibrate(self, on_progress: ProgressCallback) -> CalibrationResult:
        """Simulate a spin-down: the rider speeds up, stops pedalling, the flywheel coasts.

        Args:
            on_progress (ProgressCallback): Called with every progress update.

        Returns:
            CalibrationResult: The outcome.

        Raises:
            CalibrationError: If ``world.calibration_fails`` is set.
        """
        low, high = self.SPIN_DOWN_TARGET
        dt = self.world.tick_s / 2
        on_progress(
            CalibrationProgress(CalibrationPhase.STARTING, "Asking the trainer for a spin-down…")
        )
        await asyncio.sleep(dt)
        speed = 20.0
        self.calibration_speed = speed
        try:
            while speed < high:
                speed = min(high + 1, speed + random.uniform(1.5, 3.0))
                self.calibration_speed = speed
                on_progress(
                    CalibrationProgress(
                        CalibrationPhase.SPEED_UP,
                        f"Pedal up to {high:.0f} km/h and hold it until the trainer says stop.",
                        speed,
                        low,
                        high,
                        min(1.0, speed / high),
                    )
                )
                await asyncio.sleep(dt)
            start = speed
            while speed > 1:
                speed *= 0.75
                self.calibration_speed = speed
                on_progress(
                    CalibrationProgress(
                        CalibrationPhase.COAST,
                        "Stop pedalling and let the flywheel coast. Don't touch the pedals.",
                        speed,
                        low,
                        high,
                        1 - speed / start,
                    )
                )
                await asyncio.sleep(dt)
                if self.world.calibration_fails and speed < start / 2:
                    raise CalibrationError("The trainer reported a spin-down error (simulated).")
        finally:
            self.calibration_speed = None
        return CalibrationResult("Spin-down calibration complete.")

    def step(self) -> None:
        """Move the rider's true power towards the mode's goal and emit a biased reading."""
        w = self.world
        if self.calibration_speed is not None:  # spinning down: only the flywheel speed
            self.emit(Reading(self.id, power=0, cadence=0, speed_kmh=self.calibration_speed))
            return
        if self.mode == "erg":
            goal = self.value / w.trainer_bias  # trainer closes the loop on its OWN reading
        elif self.mode == "resistance":
            goal = 60 + 2.4 * self.value
        else:
            goal = max(40.0, 150 + 30 * self.value)
        w.true_power += (goal - w.true_power) * 0.5
        w.cadence = 90 + random.uniform(-2, 2)
        measured = w.true_power * w.trainer_bias + random.uniform(-3, 3)
        speed = 3.6 * (max(w.true_power, 0) / 0.9) ** (1 / 3) * 1.0
        self.emit(Reading(self.id, power=round(measured), cadence=w.cadence, speed_kmh=speed))


@register
class FakePowerMeter(_FakeBase, Calibratable):
    """Simulated power meter reporting the rider's true power with noise."""

    DRIVER = DriverId.FAKE_POWER
    ROLES = frozenset({Role.POWER, Role.CADENCE})
    CALIBRATION = CalibrationKind.ZERO_OFFSET

    def __init__(self, address: str = "fake-pedals", name: str = "Fake Pedals", world=None):
        """Initialize the fake power meter.

        Args:
            address (str): Fake device address.
            name (str): Human-readable device name.
            world (FakeWorld | None): Simulation state; defaults to the global ``WORLD``.
        """
        super().__init__(address, name, world)

    async def calibrate(self, on_progress: ProgressCallback) -> CalibrationResult:
        """Simulate a zero-offset calibration.

        Args:
            on_progress (ProgressCallback): Called with every progress update.

        Returns:
            CalibrationResult: The outcome, with a simulated raw offset.

        Raises:
            CalibrationError: If ``world.calibration_fails`` is set.
        """
        steps = 6
        for i in range(steps):
            on_progress(
                CalibrationProgress(
                    CalibrationPhase.MEASURING,
                    "Measuring the zero offset: keep the cranks still…",
                    fraction=i / steps,
                )
            )
            await asyncio.sleep(self.world.tick_s / 2)
        if self.world.calibration_fails:
            raise CalibrationError(
                "Calibration failed (operation failed). Unclip, keep the cranks still with "
                "no weight on the pedals, and try again. (simulated)"
            )
        offset = 512 + random.randint(-20, 20)
        return CalibrationResult(
            f"Zero offset calibration complete: offset {offset} ({offset / 32:.2f} N·m).", offset
        )

    def step(self) -> None:
        """Emit a noisy power/cadence/balance reading unless the pedals are "dead"."""
        w = self.world
        if not w.pedals_alive:
            return
        self.emit(
            Reading(
                self.id,
                power=round(w.true_power + random.uniform(-4, 4)),
                cadence=w.cadence + random.uniform(-1, 1),
                balance_left=50 + random.uniform(-2, 2),
            )
        )


@register
class FakeHeartRate(_FakeBase):
    """Simulated heart-rate monitor whose HR follows the rider's power."""

    DRIVER = DriverId.FAKE_HR
    ROLES = frozenset({Role.HR})

    def __init__(self, address: str = "fake-hr", name: str = "Fake HRM", world=None):
        """Initialize the fake heart-rate monitor.

        Args:
            address (str): Fake device address.
            name (str): Human-readable device name.
            world (FakeWorld | None): Simulation state; defaults to the global ``WORLD``.
        """
        super().__init__(address, name, world)

    def step(self) -> None:
        """Move the heart rate towards a power-dependent steady state and emit it."""
        w = self.world
        w.hr += ((60 + 0.42 * w.true_power) - w.hr) * 0.05
        self.emit(Reading(self.id, hr=round(w.hr)))


def fake_devices() -> list[Device]:
    """Create one of each simulated device.

    Returns:
        list[Device]: A fake trainer, power meter and heart-rate monitor.
    """
    return [FakeTrainer(), FakePowerMeter(), FakeHeartRate()]
