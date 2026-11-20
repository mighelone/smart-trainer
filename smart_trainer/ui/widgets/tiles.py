"""Metric tiles and their zone colours and badges."""

from __future__ import annotations

from nicegui import ui

from smart_trainer.config import DEFAULT_POWER_ZONES


class Tile:
    """A metric tile with a uniform layout: label, value, unit, zone badge and sub-line.

    Every tile has the same structure (and the sub-line always reserves its space), so
    tiles in a grid line up regardless of what they currently show. The zone is shown
    as a colored accent bar and a ``Zn`` badge, keeping the value itself high-contrast.

    Attributes:
        card (ui.element): The tile container.
        value (ui.label): Label showing the main value.
        sub (ui.label): Label showing the secondary line.
        badge (ui.label): Zone badge, hidden when no zone is known.
    """

    def __init__(
        self,
        label: str,
        unit: str = "",
        icon: str | None = None,
        size: str = "md",
        accent: str | None = None,
    ) -> None:
        """Create the tile in the current UI context.

        Args:
            label (str): Metric name shown above the value.
            unit (str): Unit shown next to the value.
            icon (str | None): Material icon shown before the label.
            size (str): ``"lg"`` for primary metrics, ``"md"`` for secondary ones.
            accent (str | None): Default accent color, used when no zone is set.
        """
        self._default = accent
        self._accent: str | None = None
        self._badge: str | None = None
        with ui.element("div").classes(f"metric metric-{size}") as self.card:
            with ui.element("div").classes("metric-head"):
                with ui.element("div").classes("metric-label"):
                    if icon:
                        ui.icon(icon).classes("metric-icon")
                    ui.label(label)
                self.badge = ui.label("").classes("metric-badge")
                self.badge.set_visibility(False)
            with ui.element("div").classes("metric-body"):
                self.value = ui.label("--").classes("metric-value")
                if unit:
                    ui.label(unit).classes("metric-unit")
            self.sub = ui.label("").classes("metric-sub")
        self.set_zone(None)

    def set(self, value: str, sub: str = "") -> None:
        """Update the displayed value and sub-line.

        Args:
            value (str): Main value text.
            sub (str): Secondary line text.
        """
        self.value.set_text(value)
        self.sub.set_text(sub)

    def set_zone(self, color: str | None, badge: str | None = None) -> None:
        """Show a zone: accent color and badge text (``None`` restores the default).

        Args:
            color (str | None): CSS color of the zone.
            badge (str | None): Badge text, e.g. ``"Z3"``; hidden if ``None``.
        """
        accent = color or self._default or "var(--faint)"
        if accent != self._accent:
            self._accent = accent
            self.card.style(f"--accent: {accent}")
        if badge != self._badge:
            self._badge = badge
            self.badge.set_text(badge or "")
            self.badge.set_visibility(badge is not None)


ZONE_COLORS = ("#94a3b8", "#3b82f6", "#22c55e", "#eab308", "#f97316", "#ef4444", "#a855f7")
NO_ZONE_COLOR = "#64748b"


def zone_index(value: float, bounds: list[int] | tuple[int, ...]) -> int:
    """0-based zone of a value, given ascending zone upper bounds.

    Args:
        value (float): The value (same unit as ``bounds``).
        bounds (list[int] | tuple[int, ...]): Zone upper bounds; the last zone is open-ended.

    Returns:
        int: Zone index, at most ``len(bounds) - 1``.
    """
    for i, upper in enumerate(bounds):
        if value < upper:
            return i
    return len(bounds) - 1


def _color(i: int) -> str:
    """Color of the ``i``-th zone (the last color repeats past seven zones)."""
    return ZONE_COLORS[min(i, len(ZONE_COLORS) - 1)]


def zone_color(watts: float | None, ftp: int, zones: list[int] | None = None) -> str:
    """Color for a power value according to the rider's power zone.

    Args:
        watts (float | None): Power in watts.
        ftp (int): Rider's FTP in watts.
        zones (list[int] | None): Zone upper bounds as % of FTP; ``None`` uses Coggan zones.

    Returns:
        str: Hex color string (grey if ``watts`` is ``None``).
    """
    if watts is None or ftp <= 0:
        return NO_ZONE_COLOR
    return _color(zone_index(watts / ftp * 100, zones or DEFAULT_POWER_ZONES))


def hr_zone_color(bpm: float | None, hr_zones: list[int]) -> str | None:
    """Color for a heart rate according to the rider's HR zones.

    Args:
        bpm (float | None): Heart rate in bpm.
        hr_zones (list[int]): HR zone upper bounds in bpm.

    Returns:
        str | None: Hex color string, or ``None`` if HR or zones are unknown.
    """
    if bpm is None or not hr_zones:
        return None
    return _color(zone_index(bpm, hr_zones))


def power_zone(
    watts: float | None, ftp: int, zones: list[int] | None = None
) -> tuple[str | None, str | None]:
    """Zone color and badge text (e.g. ``"Z3"``) for a power value.

    Args:
        watts (float | None): Power in watts.
        ftp (int): Rider's FTP in watts.
        zones (list[int] | None): Zone upper bounds as % of FTP; ``None`` uses Coggan zones.

    Returns:
        tuple[str | None, str | None]: ``(color, badge)``, or ``(None, None)`` if unknown.
    """
    if watts is None or ftp <= 0:
        return None, None
    i = zone_index(watts / ftp * 100, zones or DEFAULT_POWER_ZONES)
    return _color(i), f"Z{i + 1}"


def hr_zone(bpm: float | None, hr_zones: list[int]) -> tuple[str | None, str | None]:
    """Zone color and badge text (e.g. ``"Z2"``) for a heart rate.

    Args:
        bpm (float | None): Heart rate in bpm.
        hr_zones (list[int]): HR zone upper bounds in bpm.

    Returns:
        tuple[str | None, str | None]: ``(color, badge)``, or ``(None, None)`` if unknown.
    """
    if bpm is None or not hr_zones:
        return None, None
    i = zone_index(bpm, hr_zones)
    return _color(i), f"Z{i + 1}"


CADENCE_ALERT_COLOR = "#f59e0b"  # amber, like a device that's still connecting
CADENCE_ALERT = {"low": ("LOW", "speed up"), "high": ("HIGH", "ease off")}


def cadence_display(target: int | None, alert: str | None) -> tuple[str, str | None, str | None]:
    """What the cadence tile shows besides the cadence itself.

    Args:
        target (int | None): The step's cadence target in rpm, if any.
        alert (str | None): ``"low"``, ``"high"`` or ``None`` (see ``Ride.cadence_alert``).

    Returns:
        tuple[str, str | None, str | None]: The sub-line, and the accent color and
        badge for ``Tile.set_zone`` (``None`` when on target: the tile's default).
    """
    if target is None:
        return "", None, None
    sub = f"target {target} rpm"
    if alert not in CADENCE_ALERT:
        return sub, None, None
    badge, hint = CADENCE_ALERT[alert]
    return f"{sub} · {hint}", CADENCE_ALERT_COLOR, badge
