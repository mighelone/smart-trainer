"""NiceGUI pages, built with the app's objects by :func:`register_pages`."""

from __future__ import annotations

from nicegui import ui

from smart_trainer.ui.connections import connection_forms
from smart_trainer.ui.context import AppContext, LiveRide
from smart_trainer.ui.pages.devices import DevicesPage
from smart_trainer.ui.pages.frame import frame
from smart_trainer.ui.pages.history import HistoryPage
from smart_trainer.ui.pages.mini import MiniPage
from smart_trainer.ui.pages.ride import build_ride_page
from smart_trainer.ui.pages.settings import SettingsPage
from smart_trainer.ui.pages.workouts import WorkoutsPage


def register_pages(ctx: AppContext) -> None:
    """Register every page and route, each built with the app's objects.

    Args:
        ctx (AppContext): The app's objects.
    """
    ctx.strava.register_route()
    _register_live_pages(ctx)
    _register_setup_pages(ctx)


def _register_live_pages(ctx: AppContext) -> None:
    """Register the screens used while riding: the ride page and the mini overlay.

    Args:
        ctx (AppContext): The app's objects.
    """

    @ui.page("/")
    def ride_page() -> None:
        """Ride page: live metrics, workout chart and ride controls."""
        build_ride_page(ctx)

    @ui.page("/mini")
    def mini_page() -> None:
        """Compact always-on-top overlay: key metrics and basic ride controls."""
        page = MiniPage(LiveRide.of(ctx.engine), ctx.settings, ctx.window)
        page.refresh()
        ui.timer(0.5, page.refresh)


def _register_setup_pages(ctx: AppContext) -> None:
    """Register the pages used before and after a ride: workouts, devices, history, settings.

    Args:
        ctx (AppContext): The app's objects.
    """
    engine = ctx.engine
    forms = connection_forms(ctx.strava)

    @ui.page("/workouts")
    def workouts_page() -> None:
        """Workouts page: planned workouts from the connections, built-ins and .zwo import."""
        frame(ctx, "Workouts")
        WorkoutsPage(engine.ride, engine.sync, ctx.settings)

    @ui.page("/devices")
    def devices_page() -> None:
        """Devices page: paired devices and BLE scanning/pairing."""
        frame(ctx, "Devices")
        DevicesPage(engine.devices, engine.calibration, ctx.settings)

    @ui.page("/history")
    def history_page() -> None:
        """History page: recorded sessions and manual upload."""
        frame(ctx, "History")
        HistoryPage(engine.store, engine.sync, engine.messages)

    @ui.page("/settings")
    def settings_page() -> None:
        """Settings page: rider profile, connections and power matching."""
        frame(ctx, "Settings")
        SettingsPage(
            engine.sync,
            engine.messages,
            ctx.settings,
            engine.set_spiral_guard,
            engine.set_virtual_shifting,
            forms,
        )
