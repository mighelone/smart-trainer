import asyncio

import pytest

from smart_trainer.ble import fake
from smart_trainer.ble.device import CalibrationError, CalibrationPhase
from smart_trainer.config import Settings
from smart_trainer.core.engine import Engine
from smart_trainer.core.state import Role


async def _engine(tmp_path):
    world = fake.FakeWorld(tick_s=0.01)
    e = Engine(Settings(ftp=200, auto_upload=False), sessions=tmp_path)
    trainer = fake.FakeTrainer(world=world)
    pedals = fake.FakePowerMeter(world=world)
    e.add_device(trainer, [Role.TRAINER])
    e.add_device(pedals, [Role.POWER, Role.CADENCE])
    await asyncio.sleep(0.3)
    return e, trainer, pedals, world


async def _watch(e, task):
    await asyncio.sleep(0)  # let the calibration start
    phases = []
    while not task.done():
        if e.calibration and (not phases or phases[-1] != e.calibration.phase):
            phases.append(e.calibration.phase)
        await e.tick()
        await asyncio.sleep(0.002)
    return phases


async def test_trainer_spin_down(tmp_path):
    e, trainer, _, _ = await _engine(tmp_path)
    sent = []

    async def spy(pct):
        sent.append(pct)

    trainer.set_resistance = spy
    task = asyncio.create_task(e.calibrate(trainer.id))
    phases = await _watch(e, task)
    result = task.result()
    assert "Spin-down" in result.message
    assert CalibrationPhase.SPEED_UP in phases and CalibrationPhase.COAST in phases
    assert phases.index(CalibrationPhase.SPEED_UP) < phases.index(CalibrationPhase.COAST)
    assert e.calibration and e.calibration.phase == CalibrationPhase.DONE
    assert e.calibrating_id is None
    assert sent == []  # no resistance commands during the spin-down
    await e.tick()
    assert sent  # control resumes afterwards
    await e.shutdown()


async def test_power_meter_zero_offset(tmp_path):
    e, _, pedals, _ = await _engine(tmp_path)
    task = asyncio.create_task(e.calibrate(pedals.id))
    phases = await _watch(e, task)
    result = task.result()
    assert CalibrationPhase.MEASURING in phases
    assert result.offset is not None and abs(result.offset - 512) <= 20
    assert e.calibration and e.calibration.phase == CalibrationPhase.DONE
    await e.shutdown()


async def test_calibration_failure_is_reported(tmp_path):
    e, _, pedals, world = await _engine(tmp_path)
    world.calibration_fails = True
    with pytest.raises(CalibrationError):
        await e.calibrate(pedals.id)
    assert e.calibration and e.calibration.phase == CalibrationPhase.FAILED
    assert "failed" in e.calibration.message
    assert e.calibrating_id is None
    await e.shutdown()


async def test_no_calibration_during_ride(tmp_path):
    e, trainer, _, _ = await _engine(tmp_path)
    e.start_ride(None)
    assert e.calibration_blocker(trainer.id) == "Finish the ride before calibrating."
    with pytest.raises(CalibrationError, match="Finish the ride"):
        await e.calibrate(trainer.id)
    await e.finish(upload=False)
    assert e.calibration_blocker(trainer.id) is None
    await e.shutdown()


async def test_no_ride_during_calibration(tmp_path):
    e, _, pedals, _ = await _engine(tmp_path)
    task = asyncio.create_task(e.calibrate(pedals.id))
    await asyncio.sleep(0)
    assert e.calibrating_id == pedals.id
    assert e.calibration_blocker(pedals.id) == "A calibration is already running."
    with pytest.raises(RuntimeError):
        e.start_ride(None)
    await task
    await e.shutdown()


async def test_disconnected_or_unsupported_device_refused(tmp_path):
    e, _, pedals, _ = await _engine(tmp_path)
    await pedals.stop()
    assert e.calibration_blocker(pedals.id) == "The device is not connected."
    hr = fake.FakeHeartRate(world=pedals.world)
    e.add_device(hr, [Role.HR])
    assert "does not support" in (e.calibration_blocker(hr.id) or "")
    await e.shutdown()
