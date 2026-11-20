"""NiceGUI main file for the UI tests: imports the app's pages module in a fresh simulation.

The ``user`` fixture clears all routes before a test and drops the page modules from
``sys.modules`` after it, so each test imports a fresh ``smart_trainer.ui.pages``. The
``context`` fixture in ``test_ui.py`` then registers the pages with its own engine.
"""

import sys

from nicegui import ui

sys.modules.pop("smart_trainer.ui.pages", None)  # the first test may find it already imported
import smart_trainer.ui.pages  # noqa: E402, F401  (the tests register its pages)

ui.run(storage_secret="test")
