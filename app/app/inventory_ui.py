import math

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.customer_drilldown import _firmware_attention_condition, _severe_cve_exists, _uuid_or_none, SEVERE_CVE
from app.db import SessionLocal
from app.models import Customer, Device, DeviceVulnerability, SecurityAdvisory

PER_PAGE_OPTIONS = (25, 50, 100)


def _remove_exact_route(app, path: str, method: str = "GET"):
    method = method.upper()
    app.router.routes[:] = [
        route for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def _base_scope_filters(customer_id, vendor):
    filters = []
    if customer_id:
        filters.append(Device.customer_id == customer_id)
    if vendor:
        filters.append(Device.vendor == vendor)
    return filters


def _count(db, *filters):
    stmt = select(func.count(Device.id))
    if filters:
        stmt = stmt.where(*filters)
    return int(db.scalar(stmt) or 0)


def devices(
    request: Request,
    q: str = "",
    customer: str = "",
    vendor: str = "",
    status: str = "",
    lifecycle: str = "",
    firmware: str = "",
    security: str = "",
    per_page: int = 50,
    page: int = 1,
):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)

        page = max(1, int(page or 1))
        per_page = per_page if per_page in PER_PAGE_OPTIONS else 50
        customer_id = _uuid_or_none(customer)
        term = q.strip()

        filters = _base_scope_filters(customer_id, vendor)
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
                    Device.software_id.ilike(like),
                )
            )
        if status:
            filters.append(Device.status == status)
        if lifecycle:
            filters.append(Device.lifecycle_status == lifecycle)
        if firmware == "attention":
            filters.append(_firmware_attention_condition())
        if security == "severe":
            filters.append(_severe_cve_exists())

        stmt = select(Device)
        count_stmt = select(func.count(Device.id))
        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        total = int(db.scalar(count_stmt) or 0)
        pages = max(1, math.ceil(total / per_page))
        page = min(page, pages)
        rows = list(
            db.scalars(
                stmt.options(selectinload(Device.customer), selectinload(Device.site))
                .order_by(Device.display_name.nullslast(), Device.device_identity.nullslast(), Device.name)
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
                    select(DeviceVulnerability.device_id, func.count(DeviceVulnerability.id))
                    .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
                    .where(
                        DeviceVulnerability.device_id.in_(row_ids),
                        DeviceVulnerability.status != "resolved",
                        func.lower(SecurityAdvisory.severity).in_(SEVERE_CVE),
                    )
                    .group_by(DeviceVulnerability.device_id)
                ).all()
            }

        scope = _base_scope_filters(customer_id, vendor)
        online_count = _count(db, *scope, Device.status == "online")
        offline_count = _count(db, *scope, Device.status == "offline")
        pending_count = _count(db, *scope, Device.status.in_(["pending_enrollment", "pending_link"]))
        firmware_attention_count = _count(db, *scope, _firmware_attention_condition())
        severe_count = _count(db, *scope, _severe_cve_exists())
        eol_count = _count(db, *scope, Device.lifecycle_status.in_(["eol", "eos"]))
        scope_total = _count(db, *scope)

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
            per_page=per_page,
            per_page_options=PER_PAGE_OPTIONS,
            page=page,
            pages=pages,
            total=total,
            scope_total=scope_total,
            online_count=online_count,
            offline_count=offline_count,
            pending_count=pending_count,
            firmware_attention_count=firmware_attention_count,
            severe_count=severe_count,
            eol_count=eol_count,
        )


def install_inventory_ui(app):
    _remove_exact_route(app, "/devices", "GET")
    app.add_api_route("/devices", devices, methods=["GET"], response_class=HTMLResponse, name="devices")
    promoted = []
    rest = []
    for route in app.router.routes:
        if getattr(route, "path", None) == "/devices" and "GET" in (getattr(route, "methods", set()) or set()):
            promoted.append(route)
        else:
            rest.append(route)
    app.router.routes[:] = promoted + rest
