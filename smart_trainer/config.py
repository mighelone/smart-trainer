"""User settings persisted as JSON in ~/Library/Application Support/SmartTrainer/."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, PrivateAttr

log = logging.getLogger(__name__)

APP_DIR = Path(
    os.environ.get(
        "SMART_TRAINER_HOME", Path.home() / "Library" / "Application Support" / "SmartTrainer"
    )
)
KEYRING_SERVICE = "smart-trainer"
KEYRING_INTERVALS = "intervals.icu"


class PairedDevice(BaseModel):
    address: str
    name: str
    driver: str


class PowerMatch(BaseModel):
    ratio_alpha: float = 0.08  # EMA weight for the trainer/pedal power ratio
    ki: float = 0.05  # integral gain on residual pedal error (W per W per second)
    max_offset_w: float = 60.0
    settle_s: float = 5.0
    min_cadence: float = 40.0


class Settings(BaseModel):
    ftp: int = 250
    weight_kg: float = 75.0
    intervals_athlete_id: str = "0"  # "0" = the athlete that owns the API key
    power_source: str = "pedals"  # "pedals" | "trainer"
    devices: dict[str, PairedDevice] = Field(default_factory=dict)  # device id -> paired device
    role_assignment: dict[str, str] = Field(default_factory=dict)  # role -> device id
    power_match: PowerMatch = Field(default_factory=PowerMatch)
    auto_upload: bool = True
    _readonly: bool = PrivateAttr(default=False)  # demo mode: never write config

    @classmethod
    def path(cls) -> Path:
        return APP_DIR / "config.json"

    @classmethod
    def load(cls) -> Self:
        p = cls.path()
        if p.exists():
            try:
                return cls.model_validate_json(p.read_text())
            except Exception:
                log.exception("invalid config, using defaults")
        return cls()

    def save(self) -> None:
        if self._readonly:
            return
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.model_dump(), indent=2))
        tmp.replace(p)


def sessions_dir() -> Path:
    d = APP_DIR / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_api_key() -> str | None:
    if key := os.environ.get("INTERVALS_API_KEY"):
        return key
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, KEYRING_INTERVALS)
    except Exception:
        log.exception("keyring unavailable")
        return None


def set_api_key(key: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_INTERVALS, key)
