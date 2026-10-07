import math
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select

from app import main as core
from app.db import SessionLocal
from app.models import Customer, Device, DeviceVulnerability, SecurityAdvisory

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


def vulnerabilities(
    request: Request,
    q: str = "",
    customer: str = "",
    vendor: str = "",
    severity: str = "",
    status: str = "open",
    page: int = 1,
):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "security.read"):
            raise HTTPException(403)

        page = max(1, int(page or 1))
        per_page = 50
        customer_id = _uuid_or_none(customer)
        term = q.strip()

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

        # Per-severity counts within the current scope drive the quick filters.
        severity_counts = {
            (level or "unknown").lower(): int(count)
            for level, count in db.execute(
                select(
                    func.lower(SecurityAdvisory.severity),
                    func.count(func.distinct(SecurityAdvisory.id)),
                )
                .outerjoin(DeviceVulnerability, DeviceVulnerability.advisory_id == SecurityAdvisory.id)
                .outerjoin(Device, Device.id == DeviceVulnerability.device_id)
                .where(*filters)
                .group_by(func.lower(SecurityAdvisory.severity))
            ).all()
        }
        severity_counts["severe"] = sum(severity_counts.get(level, 0) for level in SEVERE_LEVELS)
        severity_counts["all"] = sum(v for k, v in severity_counts.items() if k != "severe")

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
            .outerjoin(
                DeviceVulnerability,
                DeviceVulnerability.advisory_id == SecurityAdvisory.id,
            )
            .outerjoin(Device, Device.id == DeviceVulnerability.device_id)
        )
        if filters:
            base = base.where(*filters)
        base = base.group_by(SecurityAdvisory.id)

        count_query = select(func.count()).select_from(base.subquery())
        total = int(db.scalar(count_query) or 0)
        pages = max(1, math.ceil(total / per_page))
        page = min(page, pages)

        rows = db.execute(
            base.order_by(
                SecurityAdvisory.cvss.desc().nullslast(),
                SecurityAdvisory.published_at.desc().nullslast(),
                SecurityAdvisory.cve_id,
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
        ).all()

        customers = list(db.scalars(select(Customer).order_by(Customer.name)))
        vendors = [
            item
            for item in db.scalars(
                select(SecurityAdvisory.vendor)
                .where(SecurityAdvisory.vendor.is_not(None))
                .distinct()
                .order_by(SecurityAdvisory.vendor)
            )
            if item
        ]
        selected_customer = db.get(Customer, customer_id) if customer_id else None

        return core.render(
            request,
            db,
            user,
            "vulnerabilities.html",
            advisories=rows,
            customers=customers,
            vendors=vendors,
            selected_customer=selected_customer,
            q=term,
            customer_filter=customer,
            vendor_filter=vendor,
            severity_filter=severity,
            status_filter=status,
            page=page,
            pages=pages,
            total=total,
            severity_counts=severity_counts,
        )


def vulnerability_detail(
    request: Request,
    advisory_id: uuid.UUID,
    customer: str = "",
    status: str = "open",
):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "security.read"):
            raise HTTPException(403)

        advisory = db.get(SecurityAdvisory, advisory_id)
        if not advisory:
            raise HTTPException(404)

        customer_id = _uuid_or_none(customer)
        filters = [DeviceVulnerability.advisory_id == advisory_id]
        impact_filter = _impact_state_filter(status)
        if impact_filter is not None:
            filters.append(impact_filter)
        if customer_id:
            filters.append(Device.customer_id == customer_id)

        impacted = db.execute(
            select(DeviceVulnerability, Device, Customer)
            .join(Device, Device.id == DeviceVulnerability.device_id)
            .join(Customer, Customer.id == Device.customer_id)
            .where(*filters)
            .order_by(Customer.name, Device.display_name.nullslast(), Device.name)
        ).all()

        affected_customer_ids = list(
            db.scalars(
                select(Device.customer_id)
                .join(DeviceVulnerability, DeviceVulnerability.device_id == Device.id)
                .where(DeviceVulnerability.advisory_id == advisory_id)
                .distinct()
            )
        )
        affected_customers = (
            list(
                db.scalars(
                    select(Customer)
                    .where(Customer.id.in_(affected_customer_ids))
                    .order_by(Customer.name)
                )
            )
            if affected_customer_ids
            else []
        )
        selected_customer = db.get(Customer, customer_id) if customer_id else None

        return core.render(
            request,
            db,
            user,
            "vulnerability_detail.html",
            advisory=advisory,
            impacted=impacted,
            affected_customers=affected_customers,
            selected_customer=selected_customer,
            customer_filter=customer,
            status_filter=status,
        )


def install_security_drilldown(app):
    _remove_exact_route(app, "/security/vulnerabilities", "GET")
    _remove_exact_route(app, "/security/vulnerabilities/{advisory_id}", "GET")
    app.add_api_route(
        "/security/vulnerabilities",
        vulnerabilities,
        methods=["GET"],
        response_class=HTMLResponse,
        name="vulnerabilities",
        include_in_schema=False,
    )
    app.add_api_route(
        "/security/vulnerabilities/{advisory_id}",
        vulnerability_detail,
        methods=["GET"],
        response_class=HTMLResponse,
        name="vulnerability_detail",
        include_in_schema=False,
    )
