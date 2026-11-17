"""Visual theme shared by all pages: palette, typography and component styles."""

from __future__ import annotations

from nicegui import ui

BG = "#0b0f14"
SURFACE = "#121820"
PRIMARY = "#4f8cff"

CSS = """
:root {
  --bg: #0b0f14;
  --surface: #121820;
  --surface-2: #19212b;
  --border: rgba(255, 255, 255, 0.07);
  --text: #e8edf2;
  --muted: #8a96a3;
  --faint: #5b6774;
  --radius: 14px;
}
body, .q-page, .nicegui-content {
  background: var(--bg) !important;
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", "Segoe UI",
    Roboto, "Helvetica Neue", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.nicegui-content { padding: 0; }

/* ---------------------------------------------------------------- header */
.app-header {
  background: rgba(11, 15, 20, 0.85) !important;
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border);
  box-shadow: none !important;
}
.brand { font-weight: 650; letter-spacing: -0.01em; font-size: 1.05rem; }
.brand-page { color: var(--muted); font-weight: 400; }
.nav-btn { font-weight: 500; border-radius: 8px; }
.nav-btn.active { background: var(--surface-2); }
.dev-chip {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 9px; border-radius: 999px;
  background: var(--surface-2); border: 1px solid var(--border);
  font-size: 12px; color: var(--muted);
}
.dev-dot { width: 7px; height: 7px; border-radius: 50%; }

/* ---------------------------------------------------------------- layout */
.page { width: 100%; max-width: 1400px; margin: 0 auto; padding: 20px 24px 32px; gap: 16px; }
.page-narrow { max-width: 760px; }
.section-title {
  font-size: 0.78rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.12em; color: var(--muted);
}
.hint { color: var(--muted); font-size: 0.875rem; }
.q-card {
  background: var(--surface) !important;
  border: 1px solid var(--border);
  border-radius: var(--radius) !important;
  box-shadow: none !important;
}
.panel { padding: 16px 18px; }
.q-btn { border-radius: 10px; font-weight: 550; letter-spacing: 0; }
.q-btn-group, .q-btn-toggle { border-radius: 10px !important; box-shadow: none !important; }
.q-btn-toggle { background: var(--surface-2); border: 1px solid var(--border); }
.q-separator { background: var(--border) !important; }

/* --------------------------------------------------------- metric tiles */
.metric-grid {
  display: grid; width: 100%; gap: 12px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
@media (max-width: 860px) { .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.metric {
  --accent: var(--faint);
  position: relative; overflow: hidden;
  display: flex; flex-direction: column; justify-content: space-between;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 14px 18px 12px;
  transition: border-color 0.3s;
}
.metric::before {
  content: ""; position: absolute; left: 0; top: 0; right: 0; height: 3px;
  background: var(--accent); transition: background 0.3s;
}
.metric-head { display: flex; align-items: center; justify-content: space-between; height: 22px; }
.metric-label {
  display: flex; align-items: center; gap: 6px;
  font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.11em; color: var(--muted);
}
.metric-icon { font-size: 15px; color: var(--accent); transition: color 0.3s; }
.metric-badge {
  font-size: 0.72rem; font-weight: 700; padding: 1px 8px; border-radius: 999px;
  background: var(--accent); color: #0b0f14; font-variant-numeric: tabular-nums;
}
.metric-body { display: flex; align-items: baseline; gap: 6px; margin-top: 6px; }
.metric-value {
  font-weight: 600; letter-spacing: -0.03em; line-height: 1;
  font-variant-numeric: tabular-nums; color: var(--text); white-space: nowrap;
}
.metric-unit { font-size: 0.95rem; color: var(--muted); font-weight: 500; }
.metric-sub {
  min-height: 1.25rem; margin-top: 6px; font-size: 0.8rem; color: var(--muted);
  font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis;
}
.metric-lg { min-height: 148px; }
.metric-lg .metric-value { font-size: clamp(3rem, 5.2vw, 4.6rem); }
.metric-md { min-height: 112px; }
.metric-md .metric-value { font-size: clamp(2rem, 3.2vw, 2.6rem); }

/* -------------------------------------------------------------- ride page */
.status-bar {
  width: 100%; padding: 10px 16px; border-radius: 10px;
  background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.3);
  color: #fbbf24; font-weight: 550;
}
.step-title { font-size: 1.15rem; font-weight: 600; letter-spacing: -0.01em; }
.step-next { color: var(--muted); font-size: 0.9rem; }
.step-progress .q-linear-progress__track { opacity: 0.12; }
.control-value {
  min-width: 3.5rem; text-align: center; font-variant-numeric: tabular-nums;
  font-weight: 600; font-size: 1.05rem;
}
.field-label { color: var(--muted); font-size: 0.85rem; font-weight: 500; }
.list-title { font-weight: 600; font-size: 1rem; }
.list-sub { color: var(--muted); font-size: 0.85rem; }
"""


def apply_theme() -> None:
    """Install the palette, global CSS and component defaults on the current page."""
    ui.colors(
        primary=PRIMARY,
        secondary="#334155",
        accent="#a78bfa",
        dark=SURFACE,
        dark_page=BG,
        positive="#22c55e",
        negative="#ef4444",
        info="#38bdf8",
        warning="#f59e0b",
    )
    ui.add_css(CSS)


# Component defaults: flat, modern buttons without all-caps labels.
ui.button.default_props("unelevated no-caps")
ui.toggle.default_props("unelevated no-caps toggle-color=primary")
