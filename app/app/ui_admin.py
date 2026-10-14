"""Governance and administration for the new interface (phase 6).

- ``GET /api/ui/v1/audit/events``: audit events newest first with the same
  filters as the classic worklist (``activity_worklists._audit_query``); the
  CSV export stays the classic one, with the same filters.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app import main as core
from app.activity_worklists import _audit_query, _uuid
from app.db import SessionLocal
from app.models import AuditEvent, Customer, Device, User
from app.ui_api import ui_user
from app.ui_incidents import device_ref

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
EVENT_PAGE = 100


def _iso(value):
    return value.isoformat() if value else None


@router.get("/audit/events")
def audit_events(request: Request, event_type: str = "", result: str = "", severity: str = "", customer: str = "", device: str = "",
                 q: str = "", from_date: str = "", to_date: str = "", offset: int = 0):
    with SessionLocal() as db:
        ui_user(request, db, "audit.read")
        stmt = _audit_query(event_type, result, severity, _uuid(customer), device, q, from_date, to_date)
        total = int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
        offset = max(0, int(offset or 0))
        events = list(db.scalars(stmt.order_by(AuditEvent.timestamp.desc(), AuditEvent.id).offset(offset).limit(EVENT_PAGE)))

        def lookup(model, ids):
            ids = {value for value in ids if value}
            return {row.id: row for row in db.scalars(select(model).where(model.id.in_(ids)))} if ids else {}

        customers = lookup(Customer, (event.customer_id for event in events))
        devices = lookup(Device, (event.device_id for event in events))
        actors = lookup(User, (event.actor_user_id for event in events))
        return {
            "total": total, "offset": offset, "limit": EVENT_PAGE,
            "event_types": list(db.scalars(select(AuditEvent.event_type).distinct().order_by(AuditEvent.event_type))),
            "results": [value for value in db.scalars(select(AuditEvent.result).distinct().order_by(AuditEvent.result)) if value],
            "items": [{
                "id": str(event.id), "timestamp": _iso(event.timestamp), "event_type": event.event_type,
                "label": core.event_label(event.event_type), "severity": event.severity, "result": event.result, "source": event.source,
                "customer": {"id": str(event.customer_id), "name": customers[event.customer_id].name} if event.customer_id in customers else None,
                "device": device_ref(devices.get(event.device_id)),
                "actor": actors[event.actor_user_id].username if event.actor_user_id in actors else None,
                "details": event.details or {},
            } for event in events],
        }


def install_ui_admin(app) -> None:
    app.include_router(router)
