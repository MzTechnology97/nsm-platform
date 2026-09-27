import math
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.db import SessionLocal
from app.models import Customer, Device

LIFECYCLE_ATTENTION_STATES = ("eol", "eos")


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


def lifecycle(
    request: Request,
    q: str = "",
    customer: str = "",
    vendor: str = "",
    state: str = "",
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

        filters = [func.lower(Device.lifecycle_status).in_(LIFECYCLE_ATTENTION_STATES)]
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
                )
            )
        if customer_id:
            filters.append(Device.customer_id == customer_id)
        if vendor:
            filters.append(func.lower(Device.vendor) == vendor.lower())
        if state in LIFECYCLE_ATTENTION_STATES:
            filters.append(func.lower(Device.lifecycle_status) == state)

        total = int(db.scalar(select(func.count(Device.id)).where(*filters)) or 0)
        pages = max(1, math.ceil(total / per_page))
        page = min(page, pages)

        rows = list(
            db.scalars(
                select(Device)
                .where(*filters)
                .options(selectinload(Device.customer), selectinload(Device.site))
                .order_by(
                    Device.lifecycle_status,
                    Device.vendor,
                    Device.display_name.nullslast(),
                    Device.name,
                )
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        )

        summary_filters = [func.lower(Device.lifecycle_status).in_(LIFECYCLE_ATTENTION_STATES)]
        if customer_id:
            summary_filters.append(Device.customer_id == customer_id)
        if vendor:
            summary_filters.append(func.lower(Device.vendor) == vendor.lower())
        if term:
            like = f"%{term}%"
            summary_filters.append(
                or_(
                    Device.display_name.ilike(like),
                    Device.device_identity.ilike(like),
                    Device.name.ilike(like),
                    Device.vendor.ilike(like),
                    Device.model.ilike(like),
                    Device.serial_number.ilike(like),
                )
            )

        eol_count = int(
            db.scalar(
                select(func.count(Device.id)).where(
                    *summary_filters,
                    func.lower(Device.lifecycle_status) == "eol",
                )
            )
            or 0
        )
        eos_count = int(
            db.scalar(
                select(func.count(Device.id)).where(
                    *summary_filters,
                    func.lower(Device.lifecycle_status) == "eos",
                )
            )
            or 0
        )
        customer_count = int(
            db.scalar(
                select(func.count(func.distinct(Device.customer_id))).where(*summary_filters)
            )
            or 0
        )

        customers = list(db.scalars(select(Customer).order_by(Customer.name)))
        vendors = [
            item
            for item in db.scalars(
                select(Device.vendor)
                .where(func.lower(Device.lifecycle_status).in_(LIFECYCLE_ATTENTION_STATES))
                .distinct()
                .order_by(Device.vendor)
            )
            if item
        ]
        selected_customer = db.get(Customer, customer_id) if customer_id else None

        return core.render(
            request,
            db,
            user,
            "lifecycle.html",
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
            eol_count=eol_count,
            eos_count=eos_count,
            customer_count=customer_count,
        )


def install_lifecycle_drilldown(app):
    _remove_exact_route(app, "/security/lifecycle", "GET")
    app.add_api_route(
        "/security/lifecycle",
        lifecycle,
        methods=["GET"],
        response_class=HTMLResponse,
        name="lifecycle",
        include_in_schema=False,
    )
