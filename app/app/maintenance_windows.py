"""Planned maintenance windows: periods when a customer's devices (all, or one) may be down without counting against availability or the SLA.

- ``outside_maintenance(db, lower, upper)``: SQL condition on ``DevicePingSample``
  that keeps only the samples outside every window overlapping the period
  (used by ``availability`` and ``sla``); ``true`` when there is none;
- ``GET /api/ui/v1/maintenance?month=`` (``reports.read``): the windows that
  overlap the month, with ``retroactive`` when a window was written after it
  had started (it then changes an SLA already measured, so it is shown);
- ``POST /api/ui/v1/maintenance`` and ``DELETE .../{id}`` (``customers.write``):
  create or remove a window, audited (``MAINTENANCE_WINDOW_CREATED`` /
  ``MAINTENANCE_WINDOW_DELETED``).

Windows are single periods (no recurrence), at most 7 days long and at most a
year back. Customer scope applies through the global ORM filter.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, not_, or_, select, true

from app import main as core
from app.agent_models import DevicePingSample
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import Customer, Device, MaintenanceWindow, User, utcnow
from app.ui_api import brand_icon, require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
MAX_LENGTH = timedelta(days=7)
MAX_BACK = timedelta(days=366)
MAX_AHEAD = timedelta(days=366)
RETROACTIVE_SLACK = timedelta(minutes=5)
REASON_MIN, REASON_MAX = 3, 500


def windows_between(db, lower: datetime, upper: datetime) -> list[MaintenanceWindow]:
    return list(db.scalars(select(MaintenanceWindow).where(MaintenanceWindow.starts_at < upper, MaintenanceWindow.ends_at > lower)
                           .order_by(MaintenanceWindow.starts_at)))


def outside_maintenance(db, lower: datetime, upper: datetime):
    """Condition true for a ping sample that is not inside a maintenance window of its device or customer."""
    inside = []
    for window in windows_between(db, lower, upper):
        period = and_(DevicePingSample.observed_at >= window.starts_at, DevicePingSample.observed_at < window.ends_at)
        if window.device_id:
            inside.append(and_(DevicePingSample.device_id == window.device_id, period))
        else:
            inside.append(and_(DevicePingSample.device_id.in_(select(Device.id).where(Device.customer_id == window.customer_id)), period))
    return not_(or_(*inside)) if inside else true()


def customer_paused_minutes(db, lower: datetime, upper: datetime) -> dict:
    """Per customer: minutes of the period covered by windows on all its devices (overlaps merged)."""
    spans: dict = {}
    for window in windows_between(db, lower, upper):
        if window.device_id is None:
            spans.setdefault(window.customer_id, []).append((max(window.starts_at, lower), min(window.ends_at, upper)))
    out = {}
    for customer_id, items in spans.items():
        total, end = 0.0, None
        for start, stop in sorted(items):
            if end is not None and start < end:
                start = end
            if stop > start:
                total += (stop - start).total_seconds() / 60
                end = stop if end is None else max(end, stop)
        out[customer_id] = total
    return out


def _parse(value, code: str) -> datetime:
    try:
        when = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        raise InputError(code, "Data e ora non valide.") from None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def create_record(db, user: User, body: dict) -> MaintenanceWindow:
    """Validate and store a window, audited. Commits.

    ``InputError`` codes: ``customer``, ``device`` (not of that customer), ``starts_at`` / ``ends_at`` (unreadable),
    ``range`` (end not after start), ``length`` (over 7 days), ``when`` (more than a year back or ahead), ``reason``.
    """
    customer = db.get(Customer, _uuid(body.get("customer_id")) or uuid.UUID(int=0))
    if not customer:
        raise InputError("customer", "Scegli il cliente.")
    device = None
    if body.get("device_id"):
        device = db.get(Device, _uuid(body.get("device_id")) or uuid.UUID(int=0))
        if not device or device.customer_id != customer.id:
            raise InputError("device", "L'apparato non appartiene al cliente.")
    starts, ends = _parse(body.get("starts_at"), "starts_at"), _parse(body.get("ends_at"), "ends_at")
    if ends <= starts:
        raise InputError("range", "La fine deve essere dopo l'inizio.")
    if ends - starts > MAX_LENGTH:
        raise InputError("length", "Una finestra dura al massimo 7 giorni.")
    now = utcnow()
    if starts < now - MAX_BACK or starts > now + MAX_AHEAD:
        raise InputError("when", "La finestra deve essere entro un anno da oggi.")
    reason = str(body.get("reason") or "").strip()
    if not REASON_MIN <= len(reason) <= REASON_MAX:
        raise InputError("reason", f"Indica il motivo ({REASON_MIN}–{REASON_MAX} caratteri).")
    window = MaintenanceWindow(customer_id=customer.id, device_id=device.id if device else None, starts_at=starts, ends_at=ends, reason=reason,
                               created_by_user_id=user.id, created_by_name=user.username)
    db.add(window)
    db.flush()
    core.add_event(db, "MAINTENANCE_WINDOW_CREATED", actor=user, customer_id=customer.id, device_id=device.id if device else None,
                   details={"window_id": str(window.id), "starts_at": starts.isoformat(), "ends_at": ends.isoformat(), "reason": reason,
                            "retroactive": _retroactive(window)})
    db.commit()
    return window


def delete_record(db, user: User, window_id: uuid.UUID) -> None:
    window = db.get(MaintenanceWindow, window_id)
    if not window:
        raise HTTPException(404)
    core.add_event(db, "MAINTENANCE_WINDOW_DELETED", actor=user, customer_id=window.customer_id, device_id=window.device_id,
                   details={"window_id": str(window.id), "starts_at": window.starts_at.isoformat(), "ends_at": window.ends_at.isoformat(),
                            "reason": window.reason, "created_by": window.created_by_name})
    db.delete(window)
    db.commit()


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _retroactive(window: MaintenanceWindow) -> bool:
    return bool(window.created_at and window.created_at > window.starts_at + RETROACTIVE_SLACK)


def _row(window: MaintenanceWindow, customers: dict, devices: dict, now: datetime) -> dict:
    device = devices.get(window.device_id)
    customer = customers.get(window.customer_id)
    state = "planned" if window.starts_at > now else "active" if window.ends_at > now else "done"
    return {"id": str(window.id), "customer": {"id": str(window.customer_id), "name": customer.name if customer else "—"},
            "device": {"id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device)} if device else None,
            "starts_at": window.starts_at, "ends_at": window.ends_at, "minutes": int((window.ends_at - window.starts_at).total_seconds() // 60),
            "reason": window.reason, "author": window.created_by_name, "created_at": window.created_at, "retroactive": _retroactive(window), "state": state}


@router.get("/maintenance")
def maintenance_list(request: Request, month: str = ""):
    from app.sla import month_bounds

    with SessionLocal() as db:
        user = ui_user(request, db, "reports.read")
        try:
            label, lower, upper, _until, _running = month_bounds(month or None)
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from None
        windows = windows_between(db, lower, upper)
        # Planned windows after the month are listed too when looking at the current month.
        now = utcnow()
        if upper > now:
            windows += [w for w in windows_between(db, upper, now + MAX_AHEAD) if w not in windows]
        customers = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_({w.customer_id for w in windows} or {uuid.UUID(int=0)})))}
        devices = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_({w.device_id for w in windows if w.device_id} or {uuid.UUID(int=0)})))}
        return jsonable_encoder({"month": label, "items": [_row(w, customers, devices, now) for w in windows if w.customer_id in customers],
                                 "can_edit": core.has_permission(user, "customers.write")})


@router.post("/maintenance", dependencies=[Depends(require_csrf)])
def maintenance_create(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.write")
        try:
            window = create_record(db, user, body)
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from None
        customers = {window.customer_id: db.get(Customer, window.customer_id)}
        devices = {window.device_id: db.get(Device, window.device_id)} if window.device_id else {}
        return jsonable_encoder(_row(window, customers, devices, utcnow()))


@router.delete("/maintenance/{window_id}", dependencies=[Depends(require_csrf)])
def maintenance_delete(request: Request, window_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.write")
        delete_record(db, user, window_id)
        return {"deleted": True}


def install_maintenance_windows(app) -> None:
    app.include_router(router)
