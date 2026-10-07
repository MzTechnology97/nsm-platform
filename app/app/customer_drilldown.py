import math
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.db import SessionLocal
from app.models import (
    ActionIssue,
    Customer,
    Device,
    DeviceVulnerability,
    SecurityAdvisory,
)

UPDATE_STATES = (
    "outdated",
    "update_available",
    "security_update",
    "critical_security_update",
    "security",
    "critical",
)
SEVERE_CVE = ("high", "critical")


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


def _firmware_attention_condition():
    return or_(
        func.lower(Device.firmware_status).in_(UPDATE_STATES),
        (
            Device.recommended_firmware_version.is_not(None)
            & or_(
                Device.firmware_version.is_(None),
                Device.recommended_firmware_version != Device.firmware_version,
            )
        ),
    )


def _severe_cve_exists():
    return exists(
        select(DeviceVulnerability.id)
        .join(
            SecurityAdvisory,
            SecurityAdvisory.id == DeviceVulnerability.advisory_id,
        )
        .where(
            DeviceVulnerability.device_id == Device.id,
            DeviceVulnerability.status != "resolved",
            func.lower(SecurityAdvisory.severity).in_(SEVERE_CVE),
        )
    )


def devices(
    request: Request,
    q: str = "",
    customer: str = "",
    vendor: str = "",
    status: str = "",
    lifecycle: str = "",
    firmware: str = "",
    security: str = "",
    page: int = 1,
):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)

        page = max(1, page)
        per_page = 50
        stmt = select(Device)
        count_stmt = select(func.count(Device.id))
        filters = []

        term = q.strip()
        if term:
            like = f"%{term}%"
            normalized_mac = core.search_mac(term)
            filters.append(
                or_(
                    Device.display_name.ilike(like),
                    Device.device_identity.ilike(like),
                    Device.name.ilike(like),
                    Device.model.ilike(like),
                    Device.serial_number.ilike(like),
                    Device.primary_mac.ilike(like),
                    Device.primary_mac == normalized_mac if normalized_mac else False,
                    Device.management_ip.ilike(like),
                )
            )

        customer_id = _uuid_or_none(customer)
        if customer_id:
            filters.append(Device.customer_id == customer_id)
        if vendor:
            filters.append(Device.vendor == vendor)
        if status:
            filters.append(Device.status == status)
        if lifecycle:
            filters.append(Device.lifecycle_status == lifecycle)
        if firmware == "attention":
            filters.append(_firmware_attention_condition())
        if security == "severe":
            filters.append(_severe_cve_exists())

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        total = db.scalar(count_stmt) or 0
        pages = max(1, math.ceil(total / per_page))
        page = min(page, pages)
        rows = list(
            db.scalars(
                stmt.options(
                    selectinload(Device.customer),
                    selectinload(Device.site),
                )
                .order_by(Device.display_name.nullslast(), Device.name)
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        )

        row_ids = [device.id for device in rows]
        severe_cve_counts = {}
        if row_ids:
            severe_cve_counts = {
                device_id: int(count or 0)
                for device_id, count in db.execute(
                    select(
                        DeviceVulnerability.device_id,
                        func.count(DeviceVulnerability.id),
                    )
                    .join(
                        SecurityAdvisory,
                        SecurityAdvisory.id == DeviceVulnerability.advisory_id,
                    )
                    .where(
                        DeviceVulnerability.device_id.in_(row_ids),
                        DeviceVulnerability.status != "resolved",
                        func.lower(SecurityAdvisory.severity).in_(SEVERE_CVE),
                    )
                    .group_by(DeviceVulnerability.device_id)
                ).all()
            }

        customers = list(db.scalars(select(Customer).order_by(Customer.name)))
        selected_customer = db.get(Customer, customer_id) if customer_id else None
        return core.render(
            request,
            db,
            user,
            "devices.html",
            devices=rows,
            customers=customers,
            severe_cve_counts=severe_cve_counts,
            selected_customer=selected_customer,
            q=term,
            customer_filter=customer,
            vendor_filter=vendor,
            status_filter=status,
            lifecycle_filter=lifecycle,
            firmware_filter=firmware,
            security_filter=security,
            page=page,
            pages=pages,
            total=total,
        )


def action_center(
    request: Request,
    severity: str = "",
    category: str = "",
    status: str = "",
    customer: str = "",
    page: int = 1,
):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()

        page = max(1, page)
        per_page = 100
        filters = []
        if severity:
            filters.append(ActionIssue.severity == severity)
        if status:
            filters.append(ActionIssue.status == status)
        else:
            filters.append(ActionIssue.status.in_(["open", "acknowledged"]))

        customer_id = _uuid_or_none(customer)
        if customer_id:
            filters.append(ActionIssue.customer_id == customer_id)

        # Category quick filters count within the other active filters.
        category_counts = [
            (name or "system", int(count))
            for name, count in db.execute(
                select(ActionIssue.category, func.count(ActionIssue.id))
                .where(*filters)
                .group_by(ActionIssue.category)
                .order_by(func.count(ActionIssue.id).desc(), ActionIssue.category)
            ).all()
        ]
        scope_total = sum(count for _, count in category_counts)
        if category:
            filters.append(ActionIssue.category == category)

        count_stmt = select(func.count(ActionIssue.id)).where(*filters)
        total = db.scalar(count_stmt) or 0
        pages = max(1, math.ceil(total / per_page))
        page = min(page, pages)

        stmt = select(ActionIssue).where(*filters)
        issues = list(
            db.scalars(
                stmt.order_by(
                    case(
                        (ActionIssue.severity == "critical", 1),
                        (ActionIssue.severity == "high", 2),
                        (ActionIssue.severity.in_(["warning", "medium"]), 3),
                        (ActionIssue.severity == "low", 4),
                        else_=5,
                    ),
                    ActionIssue.created_at.desc(),
                )
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        )

        device_ids = {issue.device_id for issue in issues if issue.device_id}
        customer_ids = {issue.customer_id for issue in issues if issue.customer_id}
        device_map = (
            {
                device.id: device
                for device in db.scalars(
                    select(Device).where(Device.id.in_(device_ids))
                )
            }
            if device_ids
            else {}
        )
        customer_map = (
            {
                item.id: item
                for item in db.scalars(
                    select(Customer).where(Customer.id.in_(customer_ids))
                )
            }
            if customer_ids
            else {}
        )
        customers = list(db.scalars(select(Customer).order_by(Customer.name)))
        selected_customer = db.get(Customer, customer_id) if customer_id else None

        return core.render(
            request,
            db,
            user,
            "action_center.html",
            issues=issues,
            device_map=device_map,
            customer_map=customer_map,
            customers=customers,
            selected_customer=selected_customer,
            severity_filter=severity,
            category_filter=category,
            status_filter=status,
            customer_filter=customer,
            page=page,
            pages=pages,
            total=total,
            category_counts=category_counts,
            scope_total=scope_total,
        )


def install_customer_drilldown(app):
    _remove_exact_route(app, "/devices", "GET")
    _remove_exact_route(app, "/action-center", "GET")
    app.add_api_route(
        "/devices",
        devices,
        methods=["GET"],
        response_class=HTMLResponse,
        name="devices",
        include_in_schema=False,
    )
    app.add_api_route(
        "/action-center",
        action_center,
        methods=["GET"],
        response_class=HTMLResponse,
        name="action_center",
        include_in_schema=False,
    )
