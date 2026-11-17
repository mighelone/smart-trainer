"""PyInstaller entry point for the macOS .app (native window by default)."""

import multiprocessing

from smart_trainer.main import run

if __name__ in {"__main__", "__mp_main__"}:
    # pywebview runs in a spawned process; frozen apps need this before anything else
    multiprocessing.freeze_support()
    run()
