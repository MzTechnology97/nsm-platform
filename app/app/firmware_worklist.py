import math
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.customer_drilldown import UPDATE_STATES, _firmware_attention_condition
from app.db import SessionLocal
from app.models import Customer, Device

SECURITY_STATES = ("security_update", "critical_security_update", "security", "critical")
CRITICAL_STATES = ("critical_security_update", "critical")


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


def firmware_worklist(
    request: Request,
    q: str = "",
    customer: str = "",
    vendor: str = "",
    state: str = "attention",
    page: int = 1,
):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "firmware.read"):
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
        state_expr = _state_filter(state)
        if state_expr is not None:
            filters.append(state_expr)

        total = int(db.scalar(select(func.count(Device.id)).where(*filters)) or 0)
        pages = max(1, math.ceil(total / per_page))
        page = min(page, pages)

        rows = list(
            db.scalars(
                select(Device)
                .where(*filters)
                .options(selectinload(Device.customer), selectinload(Device.site))
                .order_by(
                    Device.customer_id,
                    Device.vendor,
                    Device.display_name.nullslast(),
                    Device.name,
                )
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        )

        scope_filters = []
        if customer_id:
            scope_filters.append(Device.customer_id == customer_id)
        if vendor:
            scope_filters.append(func.lower(Device.vendor) == vendor.lower())
        if term:
            like = f"%{term}%"
            scope_filters.append(
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

        attention_count = int(
            db.scalar(select(func.count(Device.id)).where(*scope_filters, _firmware_attention_condition())) or 0
        )
        security_count = int(
            db.scalar(
                select(func.count(Device.id)).where(
                    *scope_filters,
                    func.lower(Device.firmware_status).in_(SECURITY_STATES),
                )
            )
            or 0
        )
        critical_count = int(
            db.scalar(
                select(func.count(Device.id)).where(
                    *scope_filters,
                    func.lower(Device.firmware_status).in_(CRITICAL_STATES),
                )
            )
            or 0
        )
        unknown_count = int(
            db.scalar(
                select(func.count(Device.id)).where(
                    *scope_filters,
                    func.lower(Device.firmware_status) == "unknown",
                )
            )
            or 0
        )

        scope_total = int(db.scalar(select(func.count(Device.id)).where(*scope_filters)) or 0)

        customers = list(db.scalars(select(Customer).order_by(Customer.name)))
        vendors = [
            item
            for item in db.scalars(select(Device.vendor).distinct().order_by(Device.vendor))
            if item
        ]
        selected_customer = db.get(Customer, customer_id) if customer_id else None

        return core.render(
            request,
            db,
            user,
            "firmware_worklist.html",
            devices=rows,
            customers=customers,
            vendors=vendors,
            selected_customer=selected_customer,
            q=term,
            customer_filter=customer,
            vendor_filter=vendor,
            state_filter=state,
            total=total,
            page=page,
            pages=pages,
            attention_count=attention_count,
            security_count=security_count,
            critical_count=critical_count,
            unknown_count=unknown_count,
            scope_total=scope_total,
        )


def install_firmware_worklist(app):
    _remove_exact_route(app, "/operations/firmware", "GET")
    app.add_api_route(
        "/operations/firmware",
        firmware_worklist,
        methods=["GET"],
        response_class=HTMLResponse,
        name="firmware",
        include_in_schema=False,
    )
