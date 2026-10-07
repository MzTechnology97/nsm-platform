import math
import uuid
from datetime import timedelta

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.db import SessionLocal
from app.models import Customer, Device, utcnow

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
        today = utcnow().date()
        if state not in (*LIFECYCLE_ATTENTION_STATES, "upcoming", "unmatched"):
            state = ""

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

        def count(*extra):
            return int(db.scalar(select(func.count(Device.id)).where(*scope, *extra)) or 0)

        filters = [*scope, _state_filter(state, today)]
        groups = []
        rows = []
        if state == "unmatched":
            # One line per vendor/model: the catalog is maintained per model.
            grouped = db.execute(
                select(
                    Device.vendor,
                    Device.model,
                    Device.lifecycle_match,
                    func.count(Device.id),
                    func.count(func.distinct(Device.customer_id)),
                )
                .where(*filters)
                .group_by(Device.vendor, Device.model, Device.lifecycle_match)
                .order_by(func.count(Device.id).desc(), Device.vendor, Device.model)
            ).all()
            total = len(grouped)
            pages = max(1, math.ceil(total / per_page))
            page = min(page, pages)
            groups = grouped[(page - 1) * per_page : page * per_page]
        else:
            total = count(_state_filter(state, today))
            pages = max(1, math.ceil(total / per_page))
            page = min(page, pages)
            rows = list(
                db.scalars(
                    select(Device)
                    .where(*filters)
                    .options(selectinload(Device.customer), selectinload(Device.site))
                    .order_by(
                        Device.lifecycle_status,
                        Device.eos_date.nullslast(),
                        Device.vendor,
                        Device.display_name.nullslast(),
                        Device.name,
                    )
                    .offset((page - 1) * per_page)
                    .limit(per_page)
                )
            )

        from app.models import LifecycleRemediation
        from app.lifecycle_remediation import STATUS_LABELS as REMEDIATION_LABELS, attention_reason

        remediations = {
            r.device_id: r
            for r in db.scalars(select(LifecycleRemediation).where(LifecycleRemediation.device_id.in_([d.id for d in rows] or [None])))
        }
        remediation_rows = {
            d.id: (REMEDIATION_LABELS.get(remediations[d.id].status), bool(attention_reason(remediations[d.id], d, today)))
            for d in rows if d.id in remediations
        }
        customer_count = int(
            db.scalar(
                select(func.count(func.distinct(Device.customer_id))).where(
                    *scope, _state_filter("", today)
                )
            )
            or 0
        )
        customers = list(db.scalars(select(Customer).order_by(Customer.name)))
        vendors = [item for item in db.scalars(select(Device.vendor).distinct().order_by(Device.vendor)) if item]
        selected_customer = db.get(Customer, customer_id) if customer_id else None

        return core.render(
            request,
            db,
            user,
            "lifecycle.html",
            devices=rows,
            remediation_rows=remediation_rows,
            groups=groups,
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
            eol_count=count(_state_filter("eol", today)),
            eos_count=count(_state_filter("eos", today)),
            upcoming_count=count(_state_filter("upcoming", today)),
            unmatched_count=count(_state_filter("unmatched", today)),
            upcoming_days=UPCOMING_DAYS,
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
