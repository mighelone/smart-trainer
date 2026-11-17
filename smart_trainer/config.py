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
    """A device paired by the user.

    Attributes:
        address (str): Device address.
        name (str): Human-readable device name.
        driver (str): Driver identifier used to instantiate the device.
    """

    address: str
    name: str
    driver: str


class PowerMatch(BaseModel):
    """Tuning parameters for pedal power matching.

    Attributes:
        ratio_alpha (float): EMA weight for the trainer/pedal power ratio.
        ki (float): Integral gain on residual pedal error (W per W per second).
        max_offset_w (float): Maximum correction applied to the target, in watts.
        settle_s (float): Seconds to wait after a target change before adapting.
        min_cadence (float): Minimum cadence (rpm) to consider the rider pedalling.
    """

    ratio_alpha: float = 0.08  # EMA weight for the trainer/pedal power ratio
    ki: float = 0.05  # integral gain on residual pedal error (W per W per second)
    max_offset_w: float = 60.0
    settle_s: float = 5.0
    min_cadence: float = 40.0


class Settings(BaseModel):
    """Application settings persisted as JSON.

    Attributes:
        ftp (int): Functional threshold power in watts.
        weight_kg (float): Rider weight in kilograms.
        intervals_athlete_id (str): intervals.icu athlete id; ``"0"`` means the
            athlete that owns the API key.
        power_source (str): Preferred power source, ``"pedals"`` or ``"trainer"``.
        devices (dict[str, PairedDevice]): Paired devices by device id.
        role_assignment (dict[str, str]): Device id assigned to each role.
        power_match (PowerMatch): Pedal power matching parameters.
        auto_upload (bool): Upload finished rides to intervals.icu automatically.
    """

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
        """Location of the settings file.

        Returns:
            Path: Path to ``config.json`` inside the app directory.
        """
        return APP_DIR / "config.json"

    @classmethod
    def load(cls) -> Self:
        """Load settings from disk, falling back to defaults.

        Returns:
            Self: The loaded settings, or defaults if missing or invalid.
        """
        p = cls.path()
        if p.exists():
            try:
                return cls.model_validate_json(p.read_text())
            except Exception:
                log.exception("invalid config, using defaults")
        return cls()

    def save(self) -> None:
        """Atomically write the settings to disk (no-op in read-only/demo mode)."""
        if self._readonly:
            return
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.model_dump(), indent=2))
        tmp.replace(p)


def sessions_dir() -> Path:
    """Return the directory where ride sessions are stored, creating it if needed.

    Returns:
        Path: The sessions directory.
    """
    d = APP_DIR / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_api_key() -> str | None:
    """Get the intervals.icu API key.

    Looks at the ``INTERVALS_API_KEY`` environment variable first, then the
    system keyring.

    Returns:
        str | None: The API key, or ``None`` if not configured.
    """
    if key := os.environ.get("INTERVALS_API_KEY"):
        return key
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, KEYRING_INTERVALS)
    except Exception:
        log.exception("keyring unavailable")
        return None


def set_api_key(key: str) -> None:
    """Store the intervals.icu API key in the system keyring.

    Args:
        key (str): The API key.
    """
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_INTERVALS, key)
