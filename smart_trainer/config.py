"""User settings persisted as JSON in ~/Library/Application Support/SmartTrainer/."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, Field, PrivateAttr, model_validator

log = logging.getLogger(__name__)

APP_DIR = Path(
    os.environ.get(
        "SMART_TRAINER_HOME", Path.home() / "Library" / "Application Support" / "SmartTrainer"
    )
)
KEYRING_SERVICE = "smart-trainer"
KEYRING_INTERVALS = "intervals.icu"
# Coggan power zone upper bounds, % of FTP (the last zone is open-ended)
DEFAULT_POWER_ZONES = (55, 75, 90, 105, 120, 150, 999)


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
        min_power_w (float): Minimum average power (W) for a sample to be used when adapting.
        ratio_min (float): Lowest trainer/pedal ratio accepted as plausible.
        ratio_max (float): Highest trainer/pedal ratio accepted as plausible.
    """

    ratio_alpha: float = 0.08  # EMA weight for the trainer/pedal power ratio
    ki: float = 0.05  # integral gain on residual pedal error (W per W per second)
    max_offset_w: float = 60.0
    settle_s: float = 5.0
    min_cadence: float = 40.0
    min_power_w: float = 30.0
    ratio_min: float = 0.7
    ratio_max: float = 1.3


class ConnectionSettings(BaseModel):
    """User settings of one connection (intervals.icu, Strava, …).

    Secrets (API keys, tokens) are never stored here: they live in the keyring.

    Attributes:
        enabled (bool): Whether the connection is used at all.
        auto_upload (bool): Include this connection in the automatic upload after a ride.
        options (dict[str, Any]): Non-secret, connection-specific options
            (e.g. intervals.icu ``athlete_id``, Strava ``client_id``).
    """

    enabled: bool = True
    auto_upload: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


class Settings(BaseModel):
    """Application settings persisted as JSON.

    Attributes:
        ftp (int): Functional threshold power in watts.
        weight_kg (float): Rider weight in kilograms.
        power_source (str): Preferred power source, ``"pedals"`` or ``"trainer"``.
        devices (dict[str, PairedDevice]): Paired devices by device id.
        role_assignment (dict[str, str]): Device id assigned to each role.
        power_match (PowerMatch): Pedal power matching parameters.
        auto_upload (bool): Upload finished rides automatically (to every connection
            that has its own ``auto_upload`` on).
        sync_profile (bool): Keep FTP, weight and zones in sync with the profile connection.
        interval_cues (bool): Beep and show the next step shortly before each step change.
        erg_spiral_guard (bool): In ERG mode, let go of the trainer when the cadence
            collapses, and take the target back once spinning again.
        connections (dict[str, ConnectionSettings]): Settings of each connection by id.
        power_zones (list[int]): Power zone upper bounds as % of FTP (last is open-ended).
        power_zone_names (list[str]): Power zone names.
        hr_zones (list[int]): Heart-rate zone upper bounds in bpm.
        lthr (int | None): Lactate threshold heart rate in bpm.
        max_hr (int | None): Maximum heart rate in bpm.
        profile_synced_at (float | None): Epoch seconds of the last profile sync.
    """

    ftp: int = 250
    weight_kg: float = 75.0
    power_source: str = "pedals"  # "pedals" | "trainer"
    devices: dict[str, PairedDevice] = Field(default_factory=dict)  # device id -> paired device
    role_assignment: dict[str, str] = Field(default_factory=dict)  # role -> device id
    power_match: PowerMatch = Field(default_factory=PowerMatch)
    auto_upload: bool = True
    sync_profile: bool = True
    interval_cues: bool = True
    erg_spiral_guard: bool = True
    power_zones: list[int] = Field(default_factory=lambda: list(DEFAULT_POWER_ZONES))
    power_zone_names: list[str] = Field(default_factory=list)
    hr_zones: list[int] = Field(default_factory=list)
    lthr: int | None = None
    max_hr: int | None = None
    profile_synced_at: float | None = None
    connections: dict[str, ConnectionSettings] = Field(default_factory=dict)
    _readonly: bool = PrivateAttr(default=False)  # demo mode: never write config

    @model_validator(mode="before")
    @classmethod
    def _migrate(cls, data: Any) -> Any:
        """Move pre-connections fields (``intervals_athlete_id``) into ``connections``.

        Args:
            data (Any): Raw input data.

        Returns:
            Any: The migrated data.
        """
        if isinstance(data, dict) and "intervals_athlete_id" in data:
            data = dict(data)
            athlete = data.pop("intervals_athlete_id")
            conns = dict(data.get("connections") or {})
            iv = dict(conns.get("intervals") or {})
            iv.setdefault("options", {}).setdefault("athlete_id", athlete)
            conns["intervals"] = iv
            data["connections"] = conns
        return data

    @property
    def readonly(self) -> bool:
        """Whether changes are kept in memory only (demo mode never writes the config).

        Returns:
            bool: ``True`` in read-only mode.
        """
        return self._readonly

    @readonly.setter
    def readonly(self, value: bool) -> None:
        """Turn read-only mode on or off.

        Args:
            value (bool): ``True`` to never write the config.
        """
        self._readonly = value

    def update(self, **changes: Any) -> None:
        """Validate and apply changes, then save (all or nothing).

        A nested model (``power_match``) takes a dict of the fields to change and is
        updated in place, so components holding it see the new values.

        Args:
            **changes (Any): New values by field name, e.g. ``ftp=260``.

        Raises:
            ValueError: If a field is unknown or a value is invalid (nothing is changed).
        """
        _apply(self, changes)
        self.save()

    def update_connection(self, conn_id: str, **changes: Any) -> None:
        """Validate and apply changes to a connection's settings, then save.

        ``options`` is merged into the existing options instead of replacing them.

        Args:
            conn_id (str): Connection id.
            **changes (Any): New values by field name, e.g. ``enabled=False``.

        Raises:
            ValueError: If a field is unknown or a value is invalid (nothing is changed).
        """
        conf = self.connection(conn_id)
        if "options" in changes:
            changes = {**changes, "options": {**conf.options, **changes["options"]}}
        _apply(conf, changes)
        self.save()

    def connection(self, conn_id: str) -> ConnectionSettings:
        """Get the settings of a connection, adding the defaults if missing.

        Args:
            conn_id (str): Connection id.

        Returns:
            ConnectionSettings: The (mutable) connection settings.
        """
        return self.connections.setdefault(conn_id, ConnectionSettings())

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


def _apply(model: BaseModel, changes: dict[str, Any]) -> None:
    """Validate changes on a copy of a model, then apply them to the model in place.

    Nested models are updated field by field rather than replaced: other objects (the
    trainer controller holds ``power_match``) keep a reference to them.

    Args:
        model (BaseModel): The model to change.
        changes (dict[str, Any]): New values by field name; a dict for a nested model.

    Raises:
        ValueError: If a field is unknown or a value is invalid (``ValidationError`` is a
            ``ValueError``); the model is left unchanged.
    """
    fields = type(model).model_fields
    if unknown := sorted(set(changes) - set(fields)):
        raise ValueError(f"unknown setting: {', '.join(unknown)}")
    data = model.model_dump()
    for name, value in changes.items():
        nested = isinstance(getattr(model, name), BaseModel)
        data[name] = {**data[name], **value} if nested else value
    valid = type(model).model_validate(data)
    for name in changes:
        current, new = getattr(model, name), getattr(valid, name)
        if isinstance(current, BaseModel):
            _apply(current, new.model_dump())
        else:
            setattr(model, name, new)


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_MAX_BYTES = 1_000_000  # rotate the log file at ~1 MB
LOG_BACKUPS = 5  # keep smart-trainer.log.1 … .5


def log_path() -> Path:
    """Location of the log file.

    Returns:
        Path: Path to ``logs/smart-trainer.log`` inside the app directory.
    """
    return APP_DIR / "logs" / "smart-trainer.log"


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


def sessions_dir() -> Path:
    """Return the directory where ride sessions are stored, creating it if needed.

    Returns:
        Path: The sessions directory.
    """
    d = APP_DIR / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _secret_env(conn_id: str, name: str) -> str:
    """Environment variable that overrides a secret, e.g. ``SMART_TRAINER_STRAVA_CLIENT_SECRET``."""
    return re.sub(r"[^A-Z0-9]+", "_", f"SMART_TRAINER_{conn_id}_{name}".upper())


def get_secret(conn_id: str, name: str) -> str | None:
    """Get a connection secret from the environment or the system keyring.

    Args:
        conn_id (str): Connection id (e.g. ``"strava"``).
        name (str): Secret name (e.g. ``"refresh_token"``).

    Returns:
        str | None: The secret, or ``None`` if not set.
    """
    if value := os.environ.get(_secret_env(conn_id, name)):
        return value
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, f"{conn_id}.{name}")
    except Exception:
        log.exception("keyring unavailable")
        return None


def set_secret(conn_id: str, name: str, value: str) -> None:
    """Store a connection secret in the system keyring.

    Args:
        conn_id (str): Connection id.
        name (str): Secret name.
        value (str): Secret value.
    """
    import keyring

    keyring.set_password(KEYRING_SERVICE, f"{conn_id}.{name}", value)


def delete_secret(conn_id: str, name: str) -> None:
    """Remove a connection secret from the system keyring (no error if missing).

    Args:
        conn_id (str): Connection id.
        name (str): Secret name.
    """
    import keyring
    from keyring.errors import PasswordDeleteError

    with contextlib.suppress(PasswordDeleteError):
        keyring.delete_password(KEYRING_SERVICE, f"{conn_id}.{name}")


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
