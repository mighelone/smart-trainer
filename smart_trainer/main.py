"""Entry point: `uv run smart-trainer [--fake] [--lan] [--native] [--port 8080]`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from nicegui import app, native, ui

from smart_trainer.ble import fake
from smart_trainer.ble.scanner import make_device
from smart_trainer.config import APP_DIR, Settings
from smart_trainer.core.engine import Engine
from smart_trainer.core.state import Role
from smart_trainer.sync import registry
from smart_trainer.sync.base import Capability
from smart_trainer.ui import (
    connections,
    pages,  # noqa: F401  (registers the pages)
)
from smart_trainer.ui.pages import S

log = logging.getLogger("smart_trainer")
_background: set[asyncio.Task] = set()  # keep references so tasks aren't garbage-collected


def _startup(use_fake: bool) -> None:
    """Load settings, create the engine and its devices, and start it.

    Args:
        use_fake (bool): Use simulated devices and a read-only demo configuration.
    """
    settings = Settings.load()
    sessions = None
    if use_fake:
        # Demo mode: separate session folder, no uploads, never touch the real config
        settings = settings.model_copy(
            update={"role_assignment": {}, "devices": {}, "auto_upload": False}
        )
        settings._readonly = True
        sessions = APP_DIR / "demo-sessions"
        sessions.mkdir(parents=True, exist_ok=True)
    engine = Engine(settings, sessions=sessions)
    S.engine, S.fake = engine, use_fake
    if use_fake:
        engine.add_device(fake.FakeTrainer(), [Role.TRAINER, Role.SPEED])
        engine.add_device(fake.FakePowerMeter(), [Role.POWER, Role.CADENCE])
        engine.add_device(fake.FakeHeartRate(), [Role.HR])
    else:
        for pd in settings.devices.values():
            try:
                engine.add_device(make_device(pd.driver, pd.address, pd.name))
            except KeyError:
                log.warning("unknown driver %s for %s", pd.driver, pd.name)
    engine.start()
    log.info("engine started (data dir: %s)", APP_DIR)
    if not use_fake and settings.sync_profile and registry.connected(settings, Capability.PROFILE):
        # In the background: a slow or failing network must not block launch
        _background.add(task := asyncio.create_task(engine.sync_profile()))
        task.add_done_callback(_background.discard)


def run() -> None:
    """Parse command-line arguments and run the NiceGUI app."""
    parser = argparse.ArgumentParser(description="Smart trainer app")
    parser.add_argument("--fake", action="store_true", help="use simulated devices")
    parser.add_argument(
        "--lan",
        action="store_true",
        help="listen on the local network (open the ride screen on an iPad/phone)",
    )
    parser.add_argument(
        "--port", type=int, default=None, help="default 8080; a free port if native"
    )
    parser.add_argument("--no-browser", action="store_true")
    frozen = getattr(sys, "frozen", False)  # running from the packaged macOS .app
    parser.add_argument(
        "--native",
        action=argparse.BooleanOptionalAction,
        default=frozen,
        help="open in a desktop window instead of the browser (needs the 'app' extra)",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    app.on_startup(lambda: _startup(args.fake))
    app.on_shutdown(lambda: S.engine.shutdown())
    port = args.port
    if port is None:
        # A native window doesn't need a stable URL, so avoid clashing with other apps;
        # with --lan the iPad needs a predictable address.
        port = 8080 if args.lan or not args.native else native.find_open_port()
    connections.server_port = port  # the Strava OAuth redirect comes back to this port
    ui.run(
        title="Smart Trainer",
        dark=True,
        reload=False,
        port=port,
        host="0.0.0.0" if args.lan else "127.0.0.1",
        show=not args.no_browser,
        native=args.native,
        window_size=(1280, 860) if args.native else None,
        favicon="🚴",
    )


if __name__ in {"__main__", "__mp_main__"}:
    run()
