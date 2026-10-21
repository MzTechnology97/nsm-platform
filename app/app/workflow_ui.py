
from fastapi import APIRouter

from app import branding_palette
from app import main as core
from app.config import settings
from app.preferences import PlatformBranding

router = APIRouter()

DEFAULT_COLORS = {
    "primary": ("primary_color", branding_palette.DEFAULT_PRIMARY),
    "sidebar": ("sidebar_color", branding_palette.DEFAULT_SIDEBAR),
    "highlight": ("highlight_color", branding_palette.DEFAULT_HIGHLIGHT),
}


def _branding(db):
    item = db.get(PlatformBranding, 1)
    if item:
        return item
    item = PlatformBranding(
        id=1,
        portal_name=settings.app_name,
        tagline="Network operations & security management",
        primary_color=DEFAULT_COLORS["primary"][1],
        sidebar_color=DEFAULT_COLORS["sidebar"][1],
        highlight_color=DEFAULT_COLORS["highlight"][1],
    )
    db.add(item)
    db.flush()
    return item


def backup_schedule_label(value: str | None) -> str:
    cron = (value or "").strip()
    parts = cron.split()
    if len(parts) != 5:
        return cron or "Non definita"
    minute, hour, day, month, weekday = parts
    if minute == "0" and hour.startswith("*/") and day == "*" and month == "*" and weekday == "*":
        return f"Ogni {hour[2:]} ore"
    hh = hour.zfill(2) if hour.isdigit() else hour
    mm = minute.zfill(2) if minute.isdigit() else minute
    if day == "*" and month == "*" and weekday == "*":
        return f"Ogni giorno alle {hh}:{mm}"
    if day == "*" and month == "*" and weekday != "*":
        days = {"0": "domenica", "1": "lunedì", "2": "martedì", "3": "mercoledì", "4": "giovedì", "5": "venerdì", "6": "sabato", "7": "domenica"}
        return f"Ogni {days.get(weekday, weekday)} alle {hh}:{mm}"
    if day != "*" and month == "*" and weekday == "*":
        return f"Ogni mese, giorno {day}, alle {hh}:{mm}"
    return cron


def reset_color(db, user, color_key: str) -> str:
    """Put one brand colour back to its default (audited); returns the default. Caller commits."""
    branding = _branding(db)
    attr, default = DEFAULT_COLORS[color_key]
    old_value = getattr(branding, attr)
    setattr(branding, attr, default)
    core.add_event(
        db,
        "PLATFORM_BRANDING_COLOR_RESET",
        actor=user,
        details={"color": color_key, "old_value": old_value, "new_value": default},
    )
    return default


def install_workflow_ui(app, templates):
    app.include_router(router)
