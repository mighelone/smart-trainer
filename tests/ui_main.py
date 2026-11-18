"""NiceGUI main file for the UI tests: registers the app's pages in a fresh simulation.

The ``user`` fixture clears all routes before a test and drops the page modules from
``sys.modules`` after it, so each test imports a fresh ``smart_trainer.ui.pages``.
"""

import sys

from nicegui import ui

sys.modules.pop("smart_trainer.ui.pages", None)  # the first test may find it already imported
import smart_trainer.ui.pages  # noqa: E402, F401  (registers the pages)

ui.run(storage_secret="test")
