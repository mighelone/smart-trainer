import logging

import pytest

from smart_trainer.logs import log_path, setup_logging


@pytest.fixture
def library_configured_logging():
    """The root logger as a library leaves it (fit_tool calls basicConfig on import).

    The root logger's handlers and level are restored afterwards, pytest's own included.
    """
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    root.handlers = []
    logging.basicConfig(format="%(message)s")  # what fit_tool/utils/logging.py does
    yield
    for handler in root.handlers:
        handler.close()
    root.handlers, root.level = handlers, level


@pytest.mark.usefixtures("library_configured_logging")
def test_console_logging_shows_info_messages(capsys):
    assert setup_logging(to_file=False) is None
    logging.getLogger("smart_trainer.ble").info("Zwift Click: connected")
    assert "INFO smart_trainer.ble: Zwift Click: connected" in capsys.readouterr().err


@pytest.mark.usefixtures("library_configured_logging")
def test_file_logging_writes_info_messages():
    path = setup_logging(to_file=True)
    logging.getLogger("smart_trainer.ble").info("Zwift Click: connected")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert path == log_path()
    text = log_path().read_text(encoding="utf-8")
    assert "INFO smart_trainer.ble: Zwift Click: connected" in text
