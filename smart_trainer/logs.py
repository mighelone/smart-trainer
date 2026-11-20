"""Logging setup: a size-rotated file in the app folder, or the console."""

from __future__ import annotations

import logging
from pathlib import Path

from smart_trainer import config

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_MAX_BYTES = 1_000_000  # rotate the log file at ~1 MB
LOG_BACKUPS = 5  # keep smart-trainer.log.1 … .5


def log_path() -> Path:
    """Location of the log file.

    Returns:
        Path: Path to ``logs/smart-trainer.log`` inside the app directory.
    """
    return config.APP_DIR / "logs" / "smart-trainer.log"


def setup_logging(to_file: bool, level: int = logging.INFO) -> Path | None:
    """Log either to a size-rotated file in the app directory or to the console.

    Args:
        to_file (bool): Write to the rotating log file (e.g. in the native app, which
            has no console) instead of the console (stderr).
        level (int): Root logger level.

    Returns:
        Path | None: The log file path, or ``None`` when logging to the console.
    """
    if not to_file:
        logging.basicConfig(level=level, format=LOG_FORMAT)
        return None
    from logging.handlers import RotatingFileHandler

    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        p, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.basicConfig(level=level, handlers=[file_handler])
    return p
