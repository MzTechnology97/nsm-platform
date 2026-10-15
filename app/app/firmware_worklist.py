
from sqlalchemy import func, or_, select

from app.customer_drilldown import _firmware_attention_condition
from app.models import Device

SECURITY_STATES = ("security_update", "critical_security_update", "security", "critical")
CRITICAL_STATES = ("critical_security_update", "critical")


def _state_filter(state: str):
    if state == "attention":
        return _firmware_attention_condition()
    if state == "security":
        return func.lower(Device.firmware_status).in_(SECURITY_STATES)
    if state == "critical":
        return func.lower(Device.firmware_status).in_(CRITICAL_STATES)
    if state == "unknown":
        return func.lower(Device.firmware_status) == "unknown"
    if state == "current":
        return func.lower(Device.firmware_status).in_(("current", "ok", "up_to_date"))
    return None


def scope_filters(term: str = "", customer_id=None, vendor: str = "") -> list:
    """Search, customer and vendor filters shared by the list and its counters."""
    filters = []
    if term:
        like = f"%{term}%"
        filters.append(
            or_(
                Device.display_name.ilike(like),
                Device.device_identity.ilike(like),
                Device.name.ilike(like),
                Device.vendor.ilike(like),
                Device.model.ilike(like),
                Device.serial_number.ilike(like),
                Device.firmware_version.ilike(like),
                Device.recommended_firmware_version.ilike(like),
            )
        )
    if customer_id:
        filters.append(Device.customer_id == customer_id)
    if vendor:
        filters.append(func.lower(Device.vendor) == vendor.lower())
    return filters


def state_counts(db, filters) -> dict:
    """Devices per firmware state within the scope filters."""
    def count(*extra):
        return int(db.scalar(select(func.count(Device.id)).where(*filters, *extra)) or 0)

    return {
        "attention": count(_firmware_attention_condition()),
        "security": count(func.lower(Device.firmware_status).in_(SECURITY_STATES)),
        "critical": count(func.lower(Device.firmware_status).in_(CRITICAL_STATES)),
        "unknown": count(func.lower(Device.firmware_status) == "unknown"),
        "current": count(func.lower(Device.firmware_status).in_(("current", "ok", "up_to_date"))),
        "all": count(),
    }


