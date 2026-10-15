import uuid

from sqlalchemy import func, or_, select

from app.models import Device, DeviceVulnerability, SecurityAdvisory

SEVERE_LEVELS = ("critical", "high")


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


def _impact_state_filter(status: str):
    if status == "open":
        return DeviceVulnerability.status != "resolved"
    if status == "resolved":
        return DeviceVulnerability.status == "resolved"
    return None


def advisory_filters(term: str = "", vendor: str = "", status: str = "open", customer_id=None, device_id=None) -> list:
    """Search, vendor, impact state, customer and device filters of the advisory list."""
    filters = []
    if term:
        like = f"%{term}%"
        filters.append(
            or_(
                SecurityAdvisory.cve_id.ilike(like),
                SecurityAdvisory.vendor.ilike(like),
                SecurityAdvisory.product.ilike(like),
                SecurityAdvisory.summary.ilike(like),
            )
        )
    if vendor:
        filters.append(func.lower(SecurityAdvisory.vendor) == vendor.lower())
    impact_filter = _impact_state_filter(status)
    if impact_filter is not None:
        filters.append(impact_filter)
    if customer_id:
        filters.append(Device.customer_id == customer_id)
    if device_id:
        filters.append(Device.id == device_id)
    return filters


def advisory_severity_counts(db, filters) -> dict:
    """Advisories per severity within the scope (quick filters), plus "severe" and "all"."""
    counts = {
        (level or "unknown").lower(): int(count)
        for level, count in db.execute(
            select(func.lower(SecurityAdvisory.severity), func.count(func.distinct(SecurityAdvisory.id)))
            .outerjoin(DeviceVulnerability, DeviceVulnerability.advisory_id == SecurityAdvisory.id)
            .outerjoin(Device, Device.id == DeviceVulnerability.device_id)
            .where(*filters)
            .group_by(func.lower(SecurityAdvisory.severity))
        ).all()
    }
    counts["severe"] = sum(counts.get(level, 0) for level in SEVERE_LEVELS)
    counts["all"] = sum(v for k, v in counts.items() if k != "severe")
    return counts


def advisory_rows_query(filters, severity: str = ""):
    """Advisories with affected devices and customers, grouped per advisory (order applied by the caller)."""
    filters = list(filters)
    if severity:
        if severity.lower() == "severe":
            filters.append(func.lower(SecurityAdvisory.severity).in_(SEVERE_LEVELS))
        else:
            filters.append(func.lower(SecurityAdvisory.severity) == severity.lower())
    base = (
        select(
            SecurityAdvisory,
            func.count(func.distinct(DeviceVulnerability.device_id)).label("affected_devices"),
            func.count(func.distinct(Device.customer_id)).label("affected_customers"),
        )
        .outerjoin(DeviceVulnerability, DeviceVulnerability.advisory_id == SecurityAdvisory.id)
        .outerjoin(Device, Device.id == DeviceVulnerability.device_id)
    )
    if filters:
        base = base.where(*filters)
    return base.group_by(SecurityAdvisory.id)


ADVISORY_ORDER = (SecurityAdvisory.cvss.desc().nullslast(), SecurityAdvisory.published_at.desc().nullslast(), SecurityAdvisory.cve_id)


