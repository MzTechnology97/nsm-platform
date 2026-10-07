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

    css = f"""
:root {{
  --brand-primary: {primary} !important;
  --brand-sidebar: {sidebar} !important;
  --brand-highlight: {highlight} !important;
  --accent: {primary} !important;
  --sidebar: {sidebar} !important;
  --accent-soft: color-mix(in srgb, {primary} 12%, transparent) !important;
}}
.sidebar {{ background: {sidebar} !important; }}
a, .text-link, .customer-item:hover .customer-main strong {{ color: var(--accent-text); }}
.button.primary,
.branding-preview-button {{
  background: {primary} !important;
  border-color: {primary} !important;
  color: #fff !important;
}}
.button.primary:hover {{
  background: color-mix(in srgb, {primary} 82%, #000) !important;
  border-color: color-mix(in srgb, {primary} 82%, #000) !important;
}}
.nav-item.active {{
  background: color-mix(in srgb, {primary} 30%, transparent) !important;
}}
.avatar {{
  color: var(--accent-text) !important;
  background: color-mix(in srgb, {primary} 12%, var(--panel)) !important;
}}
input:focus, textarea:focus, select:focus {{
  border-color: {primary} !important;
  box-shadow: 0 0 0 3px color-mix(in srgb, {primary} 16%, transparent) !important;
}}
.preference-card.active,
.admin-tabs a.active {{ border-color: {primary} !important; }}
.admin-tabs a.active {{ color: var(--accent-text) !important; }}
.attention-link .nav-icon {{ color: {highlight} !important; }}
::selection {{ background: color-mix(in srgb, {primary} 32%, transparent); }}
""".strip()

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
