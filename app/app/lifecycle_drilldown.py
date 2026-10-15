import uuid
from datetime import timedelta

from sqlalchemy import and_, func, or_, select

from app.models import Device

LIFECYCLE_ATTENTION_STATES = ("eol", "eos")
UNMATCHED_STATES = ("ambiguous", "no_record", "no_model")
UPCOMING_DAYS = 365


def _remove_exact_route(app, path: str, method: str):
    method = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def _uuid_or_none(value: str):
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def _state_filter(state: str, today):
    status = func.lower(Device.lifecycle_status)
    if state in LIFECYCLE_ATTENTION_STATES:
        return status == state
    if state == "upcoming":
        horizon = today + timedelta(days=UPCOMING_DAYS)
        return and_(
            status == "supported",
            or_(Device.eos_date.between(today, horizon), Device.eol_date.between(today, horizon)),
        )
    if state == "unmatched":
        return and_(
            or_(Device.lifecycle_match.is_(None), Device.lifecycle_match.in_(UNMATCHED_STATES)),
            status == "unknown",
        )
    return status.in_(LIFECYCLE_ATTENTION_STATES)


def scope_filters(term: str = "", customer_id=None, vendor: str = "") -> list:
    """Search, customer and vendor filters shared by the list and its counters."""
    scope = []
    if term:
        like = f"%{term}%"
        scope.append(
            or_(
                Device.display_name.ilike(like),
                Device.device_identity.ilike(like),
                Device.name.ilike(like),
                Device.vendor.ilike(like),
                Device.model.ilike(like),
                Device.serial_number.ilike(like),
            )
        )
    if customer_id:
        scope.append(Device.customer_id == customer_id)
    if vendor:
        scope.append(func.lower(Device.vendor) == vendor.lower())
    return scope


def state_counts(db, scope, today) -> dict:
    """Devices per lifecycle state (EOL, EOS, ending within a year, without catalog match) and customers affected."""
    def count(*extra):
        return int(db.scalar(select(func.count(Device.id)).where(*scope, *extra)) or 0)

    counts = {key: count(_state_filter(key, today)) for key in ("eol", "eos", "upcoming", "unmatched")}
    counts["attention"] = count(_state_filter("", today))
    counts["customers"] = int(db.scalar(select(func.count(func.distinct(Device.customer_id))).where(*scope, _state_filter("", today))) or 0)
    return counts


