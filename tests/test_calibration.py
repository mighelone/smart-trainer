import asyncio
from contextlib import asynccontextmanager

import pytest

from smart_trainer.ble import fake
from smart_trainer.ble.device import CalibrationError, CalibrationPhase
from smart_trainer.config import Settings
from smart_trainer.core.engine import Engine
from smart_trainer.core.state import Role


@asynccontextmanager
async def _engine(tmp_path):
    world = fake.FakeWorld(tick_s=0.01)
    engine = Engine.from_settings(
        Settings(ftp=200, auto_upload=False), sessions=tmp_path, run_loop=False
    )
    trainer = fake.FakeTrainer(world=world)
    pedals = fake.FakePowerMeter(world=world)
    engine.devices.add(trainer, [Role.TRAINER])
    engine.devices.add(pedals, [Role.POWER, Role.CADENCE])
    await asyncio.sleep(0.3)
    async with engine:
        yield engine, trainer, pedals, world


async def _watch(engine, task):
    await asyncio.sleep(0)  # let the calibration start
    phases = []
    while not task.done():
        if engine.calibration.progress and (
            not phases or phases[-1] != engine.calibration.progress.phase
        ):
            phases.append(engine.calibration.progress.phase)
        await engine.tick()
        await asyncio.sleep(0.002)
    return phases


async def test_trainer_spin_down(tmp_path):
    async with _engine(tmp_path) as (engine, trainer, _, _):
        sent = []

        async def spy(pct):
            sent.append(pct)

        trainer.set_resistance = spy
        task = asyncio.create_task(engine.calibration.run(trainer.id))
        phases = await _watch(engine, task)
        result = task.result()
        assert "Spin-down" in result.message
        assert CalibrationPhase.SPEED_UP in phases and CalibrationPhase.COAST in phases
        assert phases.index(CalibrationPhase.SPEED_UP) < phases.index(CalibrationPhase.COAST)
        assert (
            engine.calibration.progress
            and engine.calibration.progress.phase == CalibrationPhase.DONE
        )
        assert engine.calibration.device_id is None
        assert sent == []  # no resistance commands during the spin-down
        await engine.tick()
        assert sent  # control resumes afterwards


async def test_power_meter_zero_offset(tmp_path):
    async with _engine(tmp_path) as (engine, _, pedals, _):
        task = asyncio.create_task(engine.calibration.run(pedals.id))
        phases = await _watch(engine, task)
        result = task.result()
        assert CalibrationPhase.MEASURING in phases
        assert result.offset is not None and abs(result.offset - 512) <= 20
        assert (
            engine.calibration.progress
            and engine.calibration.progress.phase == CalibrationPhase.DONE
        )


async def test_calibration_failure_is_reported(tmp_path):
    async with _engine(tmp_path) as (engine, _, pedals, world):
        world.calibration_fails = True
        with pytest.raises(CalibrationError):
            await engine.calibration.run(pedals.id)
        assert (
            engine.calibration.progress
            and engine.calibration.progress.phase == CalibrationPhase.FAILED
        )
        assert "failed" in engine.calibration.progress.message
        assert engine.calibration.device_id is None


async def test_no_calibration_during_ride(tmp_path):
    async with _engine(tmp_path) as (engine, trainer, _, _):
        engine.ride.start(None)
        assert engine.calibration.blocker(trainer.id) == "Finish the ride before calibrating."
        with pytest.raises(CalibrationError, match="Finish the ride"):
            await engine.calibration.run(trainer.id)
        await engine.ride.finish(upload=False)
        assert engine.calibration.blocker(trainer.id) is None


async def test_no_ride_during_calibration(tmp_path):
    async with _engine(tmp_path) as (engine, _, pedals, _):
        task = asyncio.create_task(engine.calibration.run(pedals.id))
        await asyncio.sleep(0)
        assert engine.calibration.device_id == pedals.id
        assert engine.calibration.blocker(pedals.id) == "A calibration is already running."
        with pytest.raises(RuntimeError):
            engine.ride.start(None)
        await task


async def test_disconnected_or_unsupported_device_refused(tmp_path):
    async with _engine(tmp_path) as (engine, _, pedals, _):
        await pedals.stop()
        assert engine.calibration.blocker(pedals.id) == "The device is not connected."
        hr = fake.FakeHeartRate(world=pedals.world)
        engine.devices.add(hr, [Role.HR])
        assert "does not support" in (engine.calibration.blocker(hr.id) or "")
