"""Command-line options of the app, and the devices it starts with."""

import pytest

from smart_trainer import main


@pytest.mark.parametrize(
    ("argv", "native", "port"),
    [
        ([], False, 8080),
        (["--port", "9000"], False, 9000),
        (["--lan", "--native"], True, 8080),  # the iPad needs a predictable address
        (["--native", "--port", "9001"], True, 9001),
    ],
)
def test_server_port(argv, native, port):
    args = main._parse_args(argv)
    assert args.native == native
    assert main._server_port(args) == port


def test_a_native_window_gets_a_free_port(monkeypatch):
    monkeypatch.setattr(main.native, "find_open_port", lambda: 54321)
    assert main._server_port(main._parse_args(["--native"])) == 54321


def test_mini_implies_native():
    args = main._parse_args(["--mini"])
    assert args.mini and args.native


async def test_pairings_whose_driver_is_gone_are_dropped(settings, tmp_path):
    from smart_trainer.config import PairedDevice, Settings
    from smart_trainer.core.engine import Engine
    from smart_trainer.core.state import Role

    settings.devices = {
        "click": PairedDevice(address="click", name="Zwift Click", driver="zwift_click"),
        "fake-hr": PairedDevice(address="fake-hr", name="Fake HR", driver="fake_hr"),
    }
    settings.role_assignment = {Role.SHIFTER.value: "click", Role.HR.value: "fake-hr"}
    settings.save()
    engine = Engine.from_settings(settings, sessions=tmp_path, run_loop=False)
    await main._add_paired(engine.devices, settings)
    assert list(engine.devices) == ["fake-hr"]
    saved = Settings.load()
    assert list(saved.devices) == ["fake-hr"]
    assert saved.role_assignment == {Role.HR.value: "fake-hr"}
    await engine.devices.stop_all()
