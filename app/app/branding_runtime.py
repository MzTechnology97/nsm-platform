from fastapi.responses import Response

from app.db import SessionLocal
from app.preferences import PlatformBranding

_DEFAULT_PRIMARY = "#1f5f8b"
_DEFAULT_SIDEBAR = "#111827"
_DEFAULT_HIGHLIGHT = "#f59e0b"


def _safe_color(value: str | None, fallback: str) -> str:
    value = (value or "").strip().lower()
    if len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
            return value
        except ValueError:
            pass
    return fallback


def branding_theme_css():
    """Serve the persisted branding palette directly from PostgreSQL."""
    with SessionLocal() as db:
        item = db.get(PlatformBranding, 1)
        primary = _safe_color(
            item.primary_color if item else None,
            _DEFAULT_PRIMARY,
        )
        sidebar = _safe_color(
            item.sidebar_color if item else None,
            _DEFAULT_SIDEBAR,
        )
        highlight = _safe_color(
            item.highlight_color if item else None,
            _DEFAULT_HIGHLIGHT,
        )

    # The sign-in pages (static/app.css) derive every shade from the --brand-* variables. A colour still at its
    # factory value is left out, so the built-in light/dark palette applies (as in the interface, Layout.tsx).
    custom = [
        (name, value)
        for name, value, factory in (
            ("--brand-primary", primary, _DEFAULT_PRIMARY),
            ("--brand-sidebar", sidebar, _DEFAULT_SIDEBAR),
            ("--brand-highlight", highlight, _DEFAULT_HIGHLIGHT),
        )
        if value != factory
    ]
    if custom:
        lines = "\n".join(f"  {name}: {value};" for name, value in custom)
        css = f":root {{\n{lines}\n}}"
    else:
        css = "/* Branding colours at their defaults: the built-in palette applies. */"

    return Response(
        content=css,
        media_type="text/css",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


def install_branding_runtime(app):
    app.add_api_route(
        "/branding/theme.css",
        branding_theme_css,
        methods=["GET"],
        include_in_schema=False,
        name="branding_theme_css",
    )
