import asyncio
from contextlib import asynccontextmanager

import pytest

from smart_trainer.ble import fake
from smart_trainer.config import Settings
from smart_trainer.core.control import SPIRAL_RELEASE_SLOPE, Mode, SpiralGuard
from smart_trainer.core.engine import Engine
from smart_trainer.core.recorder import find_unfinished
from smart_trainer.core.ride import MAX_TICK_DT_S, RideState
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
        await e.tick(now_wall=t0 + i, now_mono=t0 + i)  # simulated 1 s ticks


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

    async def counting_tick(now_wall=None, now_mono=None):
        nonlocal calls
        calls += 1
        await real_tick(now_wall, now_mono)

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


async def test_controller_timing_uses_monotonic_clock(tmp_path):
    """A wall-clock jump (NTP, sleep/wake) must not change trainer command timing."""
    async with _engine(tmp_path) as (e, trainer, world):
        sent = []

        async def spy(watts):
            sent.append(watts)

        trainer.set_erg = spy
        e.ride.start(Workout("T", [Step(600, 1.0)]))
        await e.tick(now_wall=1_700_000_000, now_mono=100.0)
        await e.tick(now_wall=1_700_000_001, now_mono=101.0)
        assert len(sent) == 1  # unchanged command: no resend within RESEND_S
        # the wall clock jumps an hour ahead, monotonic time only 1 s
        await e.tick(now_wall=1_700_003_600, now_mono=102.0)
        assert len(sent) == 1
        await e.tick(now_wall=1_700_003_610, now_mono=102.0 + e.controller.RESEND_S)
        assert len(sent) == 2  # resend is due on the monotonic clock


async def test_late_tick_advances_the_workout_by_the_real_time(tmp_path):
    async with _engine(tmp_path) as (e, trainer, world):
        e.ride.start(Workout("T", [Step(600, 1.0)]))
        await e.tick(now_wall=1_700_000_000, now_mono=100.0)
        await e.tick(now_wall=1_700_000_001, now_mono=101.0)
        await e.tick(now_wall=1_700_000_003.5, now_mono=103.5)  # 2.5 s late
        runner = e.ride.runner
        assert runner is not None
        assert runner.elapsed == 1.0 + 1.0 + 2.5
        assert e.ride.seconds == 4.5


async def test_long_stall_is_clamped(tmp_path):
    async with _engine(tmp_path) as (e, trainer, world):
        e.ride.start(Workout("T", [Step(600, 1.0)]))
        await e.tick(now_wall=1_700_000_000, now_mono=100.0)
        await e.tick(now_wall=1_700_000_060, now_mono=160.0)  # a minute without ticks
        assert e.ride.seconds == 1.0 + MAX_TICK_DT_S


async def test_resume_restores_active_time_without_crash_gap(tmp_path):
    async with _engine(tmp_path) as (e, trainer, world):
        e.ride.start(None)
        for t in (0, 1, 2, 600, 601):  # the app was down for ~10 min after t=2
            await e.tick(now_wall=1_700_000_000 + t, now_mono=100.0 + t)
        live = e.ride.seconds
    async with Engine.from_settings(
        Settings(ftp=200, auto_upload=False), sessions=tmp_path, run_loop=False
    ) as e2:
        [info] = e2.ride.recoverable_sessions()
        e2.ride.resume(info)
        assert e2.ride.seconds == live == 1 + 1 + 1 + MAX_TICK_DT_S + 1


async def test_spiral_guard_releases_and_resumes_erg(tmp_path):
    async with _engine(tmp_path) as (e, trainer, world):
        e.ride.start(Workout("T", [Step(600, 1.0)]))
        await _ride(e, 5, 1_700_000_000)
        assert trainer.mode == "erg"
        world.cadence_base = 35  # grinding to a halt
        await _ride(e, 6, 1_700_000_005)
        assert e.controller.erg_released
        assert (trainer.mode, trainer.value) == ("slope", SPIRAL_RELEASE_SLOPE)
        assert e.controller.mode == Mode.ERG and e.ride.current_target() == 200
        world.cadence_base = 85  # spun back up
        await _ride(e, 4, 1_700_000_011)
        assert not e.controller.erg_released
        assert trainer.mode == "erg" and trainer.value > 195


@pytest.mark.parametrize("on", [True, False])
def test_set_spiral_guard_saves_and_applies(tmp_path, on):
    settings = Settings(auto_upload=False, erg_spiral_guard=not on)
    settings.readonly = True
    e = Engine.from_settings(settings, sessions=tmp_path, run_loop=False)
    e.set_spiral_guard(on)
    assert settings.erg_spiral_guard is on
    assert isinstance(e.controller.guard, SpiralGuard) is on


def test_spiral_guard_follows_the_setting(tmp_path):
    on = Engine.from_settings(Settings(auto_upload=False), sessions=tmp_path, run_loop=False)
    assert isinstance(on.controller.guard, SpiralGuard)
    off = Engine.from_settings(
        Settings(auto_upload=False, erg_spiral_guard=False), sessions=tmp_path, run_loop=False
    )
    assert off.controller.guard is None and not off.controller.erg_released
