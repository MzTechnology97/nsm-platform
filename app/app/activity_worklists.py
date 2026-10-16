"""Scalable device/job and audit worklists for Core 0.39."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import String, cast, or_, select

from app import main as core
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.models import AuditEvent, Customer, Device

router = APIRouter()
ACTIVE_JOB_STATES = ("pending", "delivered", "running")


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
    app.include_router(router)
    promoted = []
    rest = []
    paths = {"/devices/{device_id}/jobs", "/audit/events/export.csv"}
    for route in app.router.routes:
        if getattr(route, "path", None) in paths:
            promoted.append(route)
        else:
            rest.append(route)
    app.router.routes[:] = promoted + rest
