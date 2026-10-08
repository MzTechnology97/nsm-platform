"""Interface icons (GUI-02): self-hosted outlined SVG sprite ``static/ui-icons.svg``.

``ui_icon('bell')`` renders an inline ``<svg><use></svg>`` (no style attribute,
CSP-safe); the stroke follows ``currentColor`` so icons adapt to theme and state.
"""
from __future__ import annotations

from markupsafe import Markup, escape

ICONS = {
    "dashboard", "customers", "devices", "monitoring", "agents", "backup", "firmware", "incidents", "vulnerabilities",
    "access", "exposure", "lifecycle", "compliance", "action", "events", "reports", "integrations", "admin", "bell", "sun", "moon",
    "menu", "search", "enter", "chevron-left", "close",
}


def ui_icon(name: str, size: int = 20, css: str = "") -> Markup:
    from app import main as core

    if name not in ICONS:
        raise ValueError(f"Unknown UI icon: {name}")
    version = core.templates.env.globals.get("asset_version", "")
    classes = f"ui-icon {escape(css)}".strip()
    return Markup(f'<svg class="{classes}" width="{int(size)}" height="{int(size)}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
                  f'<use href="/static/ui-icons.svg?v={escape(version)}#i-{name}"></use></svg>')


def install_ui_icons() -> None:
    from app import main as core

    core.templates.env.globals["ui_icon"] = ui_icon
