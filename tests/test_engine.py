import asyncio
from contextlib import asynccontextmanager

from smart_trainer.ble import fake
from smart_trainer.config import Settings
from smart_trainer.core.control import Mode
from smart_trainer.core.engine import Engine
from smart_trainer.core.recorder import find_unfinished
from smart_trainer.core.ride import RideState
from smart_trainer.core.state import Role
from smart_trainer.core.workout import Step, Workout


@asynccontextmanager
async def _engine(tmp_path, power_source="pedals"):
    world = fake.FakeWorld(trainer_bias=1.05, tick_s=0.01)
    s = Settings(ftp=200, power_source=power_source, auto_upload=False)
    e = Engine.from_settings(s, sessions=tmp_path, run_loop=False)
    trainer = fake.FakeTrainer(world=world)
    e.devices.add(trainer, [Role.TRAINER])
    e.devices.add(fake.FakePowerMeter(world=world), [Role.POWER, Role.CADENCE])
    e.devices.add(fake.FakeHeartRate(world=world), [Role.HR])
    await asyncio.sleep(0.4)
    async with e:
        yield e, trainer, world


async def _ride(e, seconds, t0):
    for i in range(seconds):
        await asyncio.sleep(0.02)  # let fake devices react
        await e.tick(now_wall=t0 + i)


async def test_erg_workout_with_pedal_power_matching(tmp_path):
    async with _engine(tmp_path) as (e, trainer, world):
        wo = Workout("Test", [Step(200, 1.0), Step(60, 1.25)])
        e.ride.start(wo)
        await _ride(e, 150, 1_700_000_000)
        assert e.controller.mode == Mode.ERG and trainer.mode == "erg"
        assert abs(world.true_power - 200) < 6, world.true_power  # pedals hit the target
        assert trainer.value > 205  # trainer was asked for more to compensate its over-reading
        fit = await e.ride.finish()
        assert fit and fit.exists() and e.ride.state == RideState.IDLE
        assert find_unfinished(tmp_path) == []


async def test_trainer_power_source_no_matching(tmp_path):
    async with _engine(tmp_path, power_source="trainer") as (e, trainer, world):
        e.ride.start(Workout("T", [Step(300, 1.0)]))
        await _ride(e, 40, 1_700_000_000)
        assert trainer.value == 200
        assert abs(world.true_power * 1.05 - 200) < 6


async def test_pedal_dropout_falls_back_to_trainer(tmp_path):
    async with _engine(tmp_path) as (e, trainer, world):
        e.ride.start(None)
        await _ride(e, 5, 1_700_000_000)
        assert e.snapshot.power_source == "fake-pedals"
        world.pedals_alive = False
        await asyncio.sleep(3.2)
        await _ride(e, 2, 1_700_000_010)
        assert e.snapshot.power_source == "fake-trainer"
        assert e.snapshot.warnings


async def test_erg_off_switches_to_resistance_and_resume(tmp_path):
    async with _engine(tmp_path) as (e, trainer, world):
        e.ride.start(Workout("T", [Step(600, 0.8)]))
        await _ride(e, 10, 1_700_000_000)
        e.ride.set_erg(False)
        await _ride(e, 2, 1_700_000_010)
        assert trainer.mode == "resistance"
    # leaving the block without finishing is crash-like: the session stays active

    s2 = Settings(ftp=200, auto_upload=False)
    async with Engine.from_settings(s2, sessions=tmp_path, run_loop=False) as e2:
        [info] = e2.ride.recoverable_sessions()
        e2.ride.resume(info)
        assert e2.ride.runner and 9 <= e2.ride.runner.elapsed <= 12


async def test_context_manager_runs_and_stops_the_loop(tmp_path):
    engine = Engine.from_settings(Settings(auto_upload=False), sessions=tmp_path, tick_s=0.01)
    calls = 0
    real_tick = engine.tick

    async def counting_tick(now_wall=None):
        nonlocal calls
        calls += 1
        await real_tick(now_wall)

    engine.tick = counting_tick
    async with engine as e:
        assert e is engine
        await asyncio.sleep(0.05)
        task = engine._task
        assert task is not None and not task.done()
    assert calls >= 3
    assert task.cancelled() and engine._task is None


async def test_workout_complete_announced_once(tmp_path):
    async with _engine(tmp_path) as (e, trainer, world):
        e.ride.start(Workout("T", [Step(3, 1.0)]))
        e.messages.append("Calibration complete")  # must not suppress the announcement
        await _ride(e, 8, 1_700_000_000)
        assert sum("Workout complete" in m for m in e.messages) == 1


async def test_finish_resets_ride_when_finalize_fails(tmp_path, monkeypatch):
    async with _engine(tmp_path) as (e, trainer, world):
        e.ride.start(Workout("T", [Step(60, 1.0)]))
        await _ride(e, 2, 1_700_000_000)

        async def boom(rec, upload=None):
            raise OSError("disk full")

        monkeypatch.setattr(e.store, "finalize", boom)
        try:
            await e.ride.finish()
        except OSError:
            pass
        else:
            raise AssertionError("finish() should propagate the failure")
        assert e.ride.state == RideState.IDLE
        assert e.ride.recorder is None and e.ride.runner is None
        assert e.controller.mode == Mode.RESISTANCE
