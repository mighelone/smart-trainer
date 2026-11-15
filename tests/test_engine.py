import asyncio

from smart_trainer.ble import fake
from smart_trainer.config import Settings
from smart_trainer.core.control import Mode
from smart_trainer.core.engine import Engine, RideState
from smart_trainer.core.recorder import find_unfinished
from smart_trainer.core.state import Role
from smart_trainer.core.workout import Step, Workout


async def _engine(tmp_path, power_source="pedals"):
    world = fake.FakeWorld(trainer_bias=1.05, tick_s=0.01)
    s = Settings(ftp=200, power_source=power_source, auto_upload=False)
    e = Engine(s, sessions=tmp_path)
    trainer = fake.FakeTrainer(world=world)
    e.add_device(trainer, [Role.TRAINER])
    e.add_device(fake.FakePowerMeter(world=world), [Role.POWER, Role.CADENCE])
    e.add_device(fake.FakeHeartRate(world=world), [Role.HR])
    await asyncio.sleep(0.4)
    return e, trainer, world


async def _ride(e, seconds, t0):
    for i in range(seconds):
        await asyncio.sleep(0.02)  # let fake devices react
        await e.tick(now_wall=t0 + i)


async def test_erg_workout_with_pedal_power_matching(tmp_path):
    e, trainer, world = await _engine(tmp_path)
    wo = Workout("Test", [Step(200, 1.0), Step(60, 1.25)])
    e.start_ride(wo)
    await _ride(e, 150, 1_700_000_000)
    assert e.controller.mode == Mode.ERG and trainer.mode == "erg"
    assert abs(world.true_power - 200) < 6, world.true_power  # pedals hit the target
    assert trainer.value > 205  # trainer was asked for more to compensate its over-reading
    fit = await e.finish()
    assert fit and fit.exists() and e.state == RideState.IDLE
    assert find_unfinished(tmp_path) == []
    await e.shutdown()


async def test_trainer_power_source_no_matching(tmp_path):
    e, trainer, world = await _engine(tmp_path, power_source="trainer")
    e.start_ride(Workout("T", [Step(300, 1.0)]))
    await _ride(e, 40, 1_700_000_000)
    assert trainer.value == 200
    assert abs(world.true_power * 1.05 - 200) < 6
    await e.shutdown()


async def test_pedal_dropout_falls_back_to_trainer(tmp_path):
    e, trainer, world = await _engine(tmp_path)
    e.start_ride(None)
    await _ride(e, 5, 1_700_000_000)
    assert e.snapshot.power_source == "fake-pedals"
    world.pedals_alive = False
    await asyncio.sleep(3.2)
    await _ride(e, 2, 1_700_000_010)
    assert e.snapshot.power_source == "fake-trainer"
    assert e.snapshot.warnings
    await e.shutdown()


async def test_erg_off_switches_to_resistance_and_resume(tmp_path):
    e, trainer, world = await _engine(tmp_path)
    e.start_ride(Workout("T", [Step(600, 0.8)]))
    await _ride(e, 10, 1_700_000_000)
    e.set_erg(False)
    await _ride(e, 2, 1_700_000_010)
    assert trainer.mode == "resistance"
    await e.shutdown()  # crash-like: session stays active

    e2 = Engine(Settings(ftp=200, auto_upload=False), sessions=tmp_path)
    [info] = e2.unfinished_sessions()
    e2.resume_session(info)
    assert e2.runner and 9 <= e2.runner.elapsed <= 12
