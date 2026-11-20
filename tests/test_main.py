"""Command-line options of the app."""

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
