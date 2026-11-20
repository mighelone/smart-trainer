"""Shared fixtures: every test runs with its own app folder and an in-memory keyring."""

import os

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from smart_trainer import config
from smart_trainer.config import Settings


class MemoryKeyring(KeyringBackend):
    """Keyring backend that keeps passwords in a dict, so tests never reach the Keychain."""

    priority = 1  # pyrefly: ignore[bad-override]  (a class property in keyring)

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.passwords.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


@pytest.fixture(autouse=True)
def isolated_app(tmp_path):
    """Keep config, sessions, logs and secrets of every test away from the user's own."""
    # Its own MonkeyPatch, not the shared fixture: requesting that here would undo a test's
    # patches only after NiceGUI's ``user`` fixture has shut its app down.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(config, "APP_DIR", tmp_path / "app")
        for name in [n for n in os.environ if n.startswith("SMART_TRAINER_")]:
            patch.delenv(name)  # secrets can come from the environment too
        previous = keyring.get_keyring()
        keyring.set_keyring(MemoryKeyring())
        yield
        keyring.set_keyring(previous)


@pytest.fixture
def settings() -> Settings:
    """Settings with a 200 W FTP and no automatic upload (saved in the test's app folder)."""
    return Settings(ftp=200, auto_upload=False)
