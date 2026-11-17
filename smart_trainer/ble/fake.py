"""Simulated devices for development and tests without hardware.

A shared FakeWorld models the rider's *true* power. The fake trainer over-reads by
`trainer_bias` (default +4 %) so pedal power matching has something to correct.
"""

from __future__ import annotations

import asyncio
import random

from smart_trainer.ble.device import ControllableTrainer, Device, DriverId, register
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
class FakeTrainer(_FakeBase, ControllableTrainer):
    """Simulated controllable smart trainer.

    Attributes:
        mode (str): Control mode, one of ``"erg"``, ``"resistance"`` or ``"slope"``.
        value (float): Target for the current mode (watts, percent or grade).
    """

    DRIVER = DriverId.FAKE_TRAINER
    ROLES = frozenset({Role.TRAINER, Role.POWER, Role.CADENCE, Role.SPEED})

    def __init__(self, address: str = "fake-trainer", name: str = "Fake Trainer", world=None):
        """Initialize the fake trainer.

        Args:
            address (str): Fake device address.
            name (str): Human-readable device name.
            world (FakeWorld | None): Simulation state; defaults to the global ``WORLD``.
        """
        super().__init__(address, name, world)
        self.mode, self.value = "resistance", 30.0

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

    def step(self) -> None:
        """Move the rider's true power towards the mode's goal and emit a biased reading."""
        w = self.world
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
class FakePowerMeter(_FakeBase):
    """Simulated power meter reporting the rider's true power with noise."""

    DRIVER = DriverId.FAKE_POWER
    ROLES = frozenset({Role.POWER, Role.CADENCE})

    def __init__(self, address: str = "fake-pedals", name: str = "Fake Pedals", world=None):
        """Initialize the fake power meter.

        Args:
            address (str): Fake device address.
            name (str): Human-readable device name.
            world (FakeWorld | None): Simulation state; defaults to the global ``WORLD``.
        """
        super().__init__(address, name, world)

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
