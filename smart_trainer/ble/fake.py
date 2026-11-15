"""Simulated devices for development and tests without hardware.

A shared FakeWorld models the rider's *true* power. The fake trainer over-reads by
`trainer_bias` (default +4 %) so pedal power matching has something to correct.
"""
from __future__ import annotations

import asyncio
import random

from smart_trainer.ble.device import ControllableTrainer, Device, register
from smart_trainer.core.state import ConnState, Reading, Role


class FakeWorld:
    def __init__(self, trainer_bias: float = 1.04, tick_s: float = 1.0) -> None:
        self.true_power = 120.0
        self.cadence = 90.0
        self.trainer_bias = trainer_bias
        self.tick_s = tick_s
        self.hr = 95.0
        self.pedals_alive = True


WORLD = FakeWorld()


class _FakeBase(Device):
    def __init__(self, address: str, name: str = "", world: FakeWorld | None = None) -> None:
        super().__init__(address, name)
        self.world = world or WORLD

    async def run_connected(self) -> None:
        await asyncio.sleep(0.2)
        self.state = ConnState.CONNECTED
        while not self._stop.is_set():
            self.step()
            await asyncio.sleep(self.world.tick_s)

    def step(self) -> None:
        raise NotImplementedError


@register
class FakeTrainer(_FakeBase, ControllableTrainer):
    DRIVER = "fake_trainer"
    ROLES = frozenset({Role.TRAINER, Role.POWER, Role.CADENCE, Role.SPEED})

    def __init__(self, address: str = "fake-trainer", name: str = "Fake Trainer", world=None):
        super().__init__(address, name, world)
        self.mode, self.value = "resistance", 30.0

    async def set_erg(self, watts: float) -> None:
        self.mode, self.value = "erg", float(watts)

    async def set_resistance(self, pct: float) -> None:
        self.mode, self.value = "resistance", float(pct)

    async def set_slope(self, grade_pct: float) -> None:
        self.mode, self.value = "slope", float(grade_pct)

    def step(self) -> None:
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
    DRIVER = "fake_power"
    ROLES = frozenset({Role.POWER, Role.CADENCE})

    def __init__(self, address: str = "fake-pedals", name: str = "Fake Pedals", world=None):
        super().__init__(address, name, world)

    def step(self) -> None:
        w = self.world
        if not w.pedals_alive:
            return
        self.emit(Reading(self.id, power=round(w.true_power + random.uniform(-4, 4)),
                          cadence=w.cadence + random.uniform(-1, 1),
                          balance_left=50 + random.uniform(-2, 2)))


@register
class FakeHeartRate(_FakeBase):
    DRIVER = "fake_hr"
    ROLES = frozenset({Role.HR})

    def __init__(self, address: str = "fake-hr", name: str = "Fake HRM", world=None):
        super().__init__(address, name, world)

    def step(self) -> None:
        w = self.world
        w.hr += ((60 + 0.42 * w.true_power) - w.hr) * 0.05
        self.emit(Reading(self.id, hr=int(round(w.hr))))


def fake_devices() -> list[Device]:
    return [FakeTrainer(), FakePowerMeter(), FakeHeartRate()]
