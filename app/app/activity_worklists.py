"""Scalable device/job and audit worklists for Core 0.39."""
from __future__ import annotations

import csv
import io
import math
import uuid
from datetime import date, datetime, time, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.models import AuditEvent, Customer, Device
from app.mikrotik_workspace import _load_device

router = APIRouter()
PAGE_SIZES = {25, 50, 100}
ACTIVE_JOB_STATES = ("pending", "delivered", "running")


def _safe_page(value: int) -> int:
    return max(1, int(value or 1))


def _safe_per_page(value: int) -> int:
    return value if value in PAGE_SIZES else 50


def _parse_day(value: str, end: bool = False):
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, "Data non valida. Usa YYYY-MM-DD.") from exc
    return datetime.combine(parsed, time.max if end else time.min, tzinfo=timezone.utc)


def _uuid(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(400, "Identificativo non valido.") from exc


def _pager(path: str, page: int, per_page: int, total: int, params: dict):
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)

    def url(target):
        payload = {k: v for k, v in params.items() if v not in (None, "")}
        payload.update({"page": target, "per_page": per_page})
        return f"{path}?{urlencode(payload)}"

    return {
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "total": total,
        "prev_url": url(page - 1) if page > 1 else None,
        "next_url": url(page + 1) if page < pages else None,
    }


def _remove_exact_route(app, path: str, method: str = "GET"):
    method = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def _device_job_query(device_id, status: str, job_type: str, q: str, from_date: str, to_date: str):
    stmt = select(DeviceJob).where(DeviceJob.device_id == device_id)
    if status:
        stmt = stmt.where(DeviceJob.status == status)
    if job_type:
        stmt = stmt.where(DeviceJob.job_type == job_type)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                DeviceJob.job_type.ilike(pattern),
                DeviceJob.status.ilike(pattern),
                DeviceJob.last_error.ilike(pattern),
                cast(DeviceJob.payload, String).ilike(pattern),
                cast(DeviceJob.result, String).ilike(pattern),
            )
        )
    start = _parse_day(from_date)
    end = _parse_day(to_date, end=True)
    if start:
        stmt = stmt.where(DeviceJob.created_at >= start)
    if end:
        stmt = stmt.where(DeviceJob.created_at <= end)
    return stmt


def _device_event_query(device_id, event_type: str, result: str, q: str, from_date: str, to_date: str):
    stmt = select(AuditEvent).where(AuditEvent.device_id == device_id)
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    if result:
        stmt = stmt.where(AuditEvent.result == result)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                AuditEvent.event_type.ilike(pattern),
                AuditEvent.source.ilike(pattern),
                AuditEvent.result.ilike(pattern),
                cast(AuditEvent.details, String).ilike(pattern),
            )
        )
    start = _parse_day(from_date)
    end = _parse_day(to_date, end=True)
    if start:
        stmt = stmt.where(AuditEvent.timestamp >= start)
    if end:
        stmt = stmt.where(AuditEvent.timestamp <= end)
    return stmt


@router.get("/devices/{device_id}/jobs", response_class=HTMLResponse, name="device_activity_worklist")
def device_activity(
    request: Request,
    device_id: uuid.UUID,
    view: str = "jobs",
    q: str = "",
    status: str = "",
    job_type: str = "",
    event_type: str = "",
    result: str = "",
    from_date: str = "",
    to_date: str = "",
    page: int = 1,
    per_page: int = 50,
):
    view = view if view in {"jobs", "audit"} else "jobs"
    page = _safe_page(page)
    per_page = _safe_per_page(per_page)

    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        device = _load_device(db, device_id)

        active_jobs = list(
            db.scalars(
                select(DeviceJob)
                .where(DeviceJob.device_id == device.id, DeviceJob.status.in_(ACTIVE_JOB_STATES))
                .order_by(DeviceJob.created_at.desc())
                .limit(25)
            )
        )
        job_types = list(db.scalars(select(DeviceJob.job_type).where(DeviceJob.device_id == device.id).distinct().order_by(DeviceJob.job_type)))
        event_types = list(db.scalars(select(AuditEvent.event_type).where(AuditEvent.device_id == device.id).distinct().order_by(AuditEvent.event_type)))

        params = {
            "view": view,
            "q": q,
            "status": status,
            "job_type": job_type,
            "event_type": event_type,
            "result": result,
            "from_date": from_date,
            "to_date": to_date,
        }

        rows = []
        if view == "jobs":
            stmt = _device_job_query(device.id, status, job_type, q, from_date, to_date)
            count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
            total = int(db.scalar(count_stmt) or 0)
            pager = _pager(f"/devices/{device.id}/jobs", page, per_page, total, params)
            rows = list(
                db.scalars(
                    stmt.order_by(DeviceJob.created_at.desc())
                    .offset((pager["page"] - 1) * per_page)
                    .limit(per_page)
                )
            )
        else:
            stmt = _device_event_query(device.id, event_type, result, q, from_date, to_date)
            count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
            total = int(db.scalar(count_stmt) or 0)
            pager = _pager(f"/devices/{device.id}/jobs", page, per_page, total, params)
            rows = list(
                db.scalars(
                    stmt.order_by(AuditEvent.timestamp.desc())
                    .offset((pager["page"] - 1) * per_page)
                    .limit(per_page)
                )
            )

        inventory = dict(device.inventory_data or {})
        return core.render(
            request,
            db,
            user,
            "device_activity.html",
            device=device,
            inventory=inventory,
            active_tab="jobs",
            view=view,
            rows=rows,
            active_jobs=active_jobs,
            job_types=job_types,
            event_types=event_types,
            pager=pager,
            filters=params,
        )


def _audit_query(event_type: str, result: str, severity: str, customer_id, device_q: str, q: str, from_date: str, to_date: str):
    stmt = select(AuditEvent)
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    if result:
        stmt = stmt.where(AuditEvent.result == result)
    if severity:
        stmt = stmt.where(AuditEvent.severity == severity)
    if customer_id:
        stmt = stmt.where(AuditEvent.customer_id == customer_id)
    if device_q:
        pattern = f"%{device_q.strip()}%"
        device_ids = select(Device.id).where(
            or_(
                Device.display_name.ilike(pattern),
                Device.device_identity.ilike(pattern),
                Device.serial_number.ilike(pattern),
                Device.primary_mac.ilike(pattern),
                Device.management_ip.ilike(pattern),
            )
        )
        stmt = stmt.where(AuditEvent.device_id.in_(device_ids))
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                AuditEvent.event_type.ilike(pattern),
                AuditEvent.source.ilike(pattern),
                AuditEvent.result.ilike(pattern),
                cast(AuditEvent.details, String).ilike(pattern),
            )
        )
    start = _parse_day(from_date)
    end = _parse_day(to_date, end=True)
    if start:
        stmt = stmt.where(AuditEvent.timestamp >= start)
    if end:
        stmt = stmt.where(AuditEvent.timestamp <= end)
    return stmt


def _audit_params(event_type, result, severity, customer, device, q, from_date, to_date):
    return {
        "event_type": event_type,
        "result": result,
        "severity": severity,
        "customer": customer,
        "device": device,
        "q": q,
        "from_date": from_date,
        "to_date": to_date,
    }


@router.get("/audit/events", response_class=HTMLResponse, name="audit_events_worklist")
def audit_events(
    request: Request,
    event_type: str = "",
    result: str = "",
    severity: str = "",
    customer: str = "",
    device: str = "",
    q: str = "",
    from_date: str = "",
    to_date: str = "",
    page: int = 1,
    per_page: int = 50,
):
    page = _safe_page(page)
    per_page = _safe_per_page(per_page)
    customer_id = _uuid(customer)
    params = _audit_params(event_type, result, severity, customer, device, q, from_date, to_date)

    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "audit.read"):
            raise HTTPException(403)

        stmt = _audit_query(event_type, result, severity, customer_id, device, q, from_date, to_date)
        total = int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
        pager = _pager("/audit/events", page, per_page, total, params)
        events = list(
            db.scalars(
                stmt.order_by(AuditEvent.timestamp.desc())
                .offset((pager["page"] - 1) * per_page)
                .limit(per_page)
            )
        )
        customer_ids = {e.customer_id for e in events if e.customer_id}
        device_ids = {e.device_id for e in events if e.device_id}
        customer_map = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_(customer_ids)))} if customer_ids else {}
        device_map = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_(device_ids)))} if device_ids else {}
        customers = list(db.scalars(select(Customer).order_by(Customer.name).limit(1000)))
        event_types = list(db.scalars(select(AuditEvent.event_type).distinct().order_by(AuditEvent.event_type)))
        export_params = {k: v for k, v in params.items() if v}
        export_url = "/audit/events/export.csv" + ("?" + urlencode(export_params) if export_params else "")
        return core.render(
            request,
            db,
            user,
            "audit_events_v2.html",
            events=events,
            customer_map=customer_map,
            device_map=device_map,
            customers=customers,
            event_types=event_types,
            pager=pager,
            filters=params,
            export_url=export_url,
        )


@router.get("/audit/events/export.csv", name="audit_events_export_csv")
def audit_events_export_csv(
    request: Request,
    event_type: str = "",
    result: str = "",
    severity: str = "",
    customer: str = "",
    device: str = "",
    q: str = "",
    from_date: str = "",
    to_date: str = "",
):
    customer_id = _uuid(customer)
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            raise HTTPException(401)
        if not core.has_permission(user, "audit.read"):
            raise HTTPException(403)
        stmt = _audit_query(event_type, result, severity, customer_id, device, q, from_date, to_date)
        events = list(db.scalars(stmt.order_by(AuditEvent.timestamp.desc()).limit(50000)))
        customer_ids = {e.customer_id for e in events if e.customer_id}
        device_ids = {e.device_id for e in events if e.device_id}
        customer_map = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_(customer_ids)))} if customer_ids else {}
        device_map = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_(device_ids)))} if device_ids else {}

        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["timestamp", "event_type", "severity", "result", "source", "customer", "device", "details"])
        for event in events:
            customer_obj = customer_map.get(event.customer_id)
            device_obj = device_map.get(event.device_id)
            writer.writerow([
                event.timestamp.isoformat() if event.timestamp else "",
                event.event_type,
                event.severity,
                event.result,
                event.source,
                customer_obj.name if customer_obj else "",
                (device_obj.display_name or device_obj.device_identity or str(device_obj.id)) if device_obj else "",
                str(event.details or {}),
            ])
        core.add_event(db, "AUDIT_EVENTS_EXPORTED", actor=user, details={"rows": len(events), "filters": _audit_params(event_type, result, severity, customer, device, q, from_date, to_date)}, source="portal")
        db.commit()
        return Response(
            out.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="nsm-audit-events.csv"'},
        )


def install_activity_worklists(app):
    _remove_exact_route(app, "/devices/{device_id}/jobs", "GET")
    _remove_exact_route(app, "/audit/events", "GET")
    app.include_router(router)
    promoted = []
    rest = []
    paths = {"/devices/{device_id}/jobs", "/audit/events", "/audit/events/export.csv"}
    for route in app.router.routes:
        if getattr(route, "path", None) in paths:
            promoted.append(route)
        else:
            rest.append(route)
    app.router.routes[:] = promoted + rest
