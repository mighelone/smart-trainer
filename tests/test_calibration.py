import asyncio

import pytest

from smart_trainer.ble import fake
from smart_trainer.ble.device import CalibrationError, CalibrationPhase
from smart_trainer.config import Settings
from smart_trainer.core.engine import Engine
from smart_trainer.core.state import Role


async def _engine(tmp_path):
    world = fake.FakeWorld(tick_s=0.01)
    e = Engine.from_settings(Settings(ftp=200, auto_upload=False), sessions=tmp_path)
    trainer = fake.FakeTrainer(world=world)
    pedals = fake.FakePowerMeter(world=world)
    e.devices.add(trainer, [Role.TRAINER])
    e.devices.add(pedals, [Role.POWER, Role.CADENCE])
    await asyncio.sleep(0.3)
    return e, trainer, pedals, world


async def _watch(e, task):
    await asyncio.sleep(0)  # let the calibration start
    phases = []
    while not task.done():
        if e.calibration.progress and (not phases or phases[-1] != e.calibration.progress.phase):
            phases.append(e.calibration.progress.phase)
        await e.tick()
        await asyncio.sleep(0.002)
    return phases


async def test_trainer_spin_down(tmp_path):
    e, trainer, _, _ = await _engine(tmp_path)
    sent = []

    async def spy(pct):
        sent.append(pct)

    trainer.set_resistance = spy
    task = asyncio.create_task(e.calibration.run(trainer.id))
    phases = await _watch(e, task)
    result = task.result()
    assert "Spin-down" in result.message
    assert CalibrationPhase.SPEED_UP in phases and CalibrationPhase.COAST in phases
    assert phases.index(CalibrationPhase.SPEED_UP) < phases.index(CalibrationPhase.COAST)
    assert e.calibration.progress and e.calibration.progress.phase == CalibrationPhase.DONE
    assert e.calibration.device_id is None
    assert sent == []  # no resistance commands during the spin-down
    await e.tick()
    assert sent  # control resumes afterwards
    await e.shutdown()


async def test_power_meter_zero_offset(tmp_path):
    e, _, pedals, _ = await _engine(tmp_path)
    task = asyncio.create_task(e.calibration.run(pedals.id))
    phases = await _watch(e, task)
    result = task.result()
    assert CalibrationPhase.MEASURING in phases
    assert result.offset is not None and abs(result.offset - 512) <= 20
    assert e.calibration.progress and e.calibration.progress.phase == CalibrationPhase.DONE
    await e.shutdown()


async def test_calibration_failure_is_reported(tmp_path):
    e, _, pedals, world = await _engine(tmp_path)
    world.calibration_fails = True
    with pytest.raises(CalibrationError):
        await e.calibration.run(pedals.id)
    assert e.calibration.progress and e.calibration.progress.phase == CalibrationPhase.FAILED
    assert "failed" in e.calibration.progress.message
    assert e.calibration.device_id is None
    await e.shutdown()


async def test_no_calibration_during_ride(tmp_path):
    e, trainer, _, _ = await _engine(tmp_path)
    e.ride.start(None)
    assert e.calibration.blocker(trainer.id) == "Finish the ride before calibrating."
    with pytest.raises(CalibrationError, match="Finish the ride"):
        await e.calibration.run(trainer.id)
    await e.ride.finish(upload=False)
    assert e.calibration.blocker(trainer.id) is None
    await e.shutdown()


async def test_no_ride_during_calibration(tmp_path):
    e, _, pedals, _ = await _engine(tmp_path)
    task = asyncio.create_task(e.calibration.run(pedals.id))
    await asyncio.sleep(0)
    assert e.calibration.device_id == pedals.id
    assert e.calibration.blocker(pedals.id) == "A calibration is already running."
    with pytest.raises(RuntimeError):
        e.ride.start(None)
    await task
    await e.shutdown()


async def test_disconnected_or_unsupported_device_refused(tmp_path):
    e, _, pedals, _ = await _engine(tmp_path)
    await pedals.stop()
    assert e.calibration.blocker(pedals.id) == "The device is not connected."
    hr = fake.FakeHeartRate(world=pedals.world)
    e.devices.add(hr, [Role.HR])
    assert "does not support" in (e.calibration.blocker(hr.id) or "")
    await e.shutdown()
