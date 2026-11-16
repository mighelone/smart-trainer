"""Entry point: `uv run smart-trainer [--fake] [--lan] [--port 8080]`."""

from __future__ import annotations

import argparse
import logging

from nicegui import app, ui

from smart_trainer.ble import fake
from smart_trainer.ble.scanner import make_device
from smart_trainer.config import APP_DIR, Settings
from smart_trainer.core.engine import Engine
from smart_trainer.core.state import Role
from smart_trainer.ui import pages  # noqa: F401  (registers the pages)
from smart_trainer.ui.pages import S

log = logging.getLogger("smart_trainer")


def _startup(use_fake: bool) -> None:
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


def run() -> None:
    parser = argparse.ArgumentParser(description="Smart trainer app")
    parser.add_argument("--fake", action="store_true", help="use simulated devices")
    parser.add_argument(
        "--lan",
        action="store_true",
        help="listen on the local network (open the ride screen on an iPad/phone)",
    )
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    app.on_startup(lambda: _startup(args.fake))
    app.on_shutdown(lambda: S.engine.shutdown())
    ui.run(
        title="Smart Trainer",
        dark=True,
        reload=False,
        port=args.port,
        host="0.0.0.0" if args.lan else "127.0.0.1",
        show=not args.no_browser,
        favicon="🚴",
    )


if __name__ in {"__main__", "__mp_main__"}:
    run()
