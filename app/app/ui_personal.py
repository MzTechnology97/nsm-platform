"""Personal pages of the new interface (phase 7): global search and notifications.

- ``GET /api/ui/v1/search?q=``: customers, sites and devices matching a term,
  the same query as the classic global search (``main.search_records``);
  devices carry their vendor icon;
- ``GET /api/ui/v1/notifications``: active notifications newest first with
  the read state of the signed-in user (``main.notification_rows``);
  ``GET .../summary`` returns only the unread count for the top bar;
- ``POST /api/ui/v1/notifications/{id}/read`` and ``.../read-all`` mark them as
  read through the functions shared with the classic forms.

Every signed-in user may use them; customer scope comes from the global ORM
filter.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from app import main as core
from app.db import SessionLocal
from app.models import Device, Notification
from app.ui_api import brand_icon, require_csrf, ui_user
from app.ui_incidents import device_ref

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])


def _safe_url(value: str | None) -> str | None:
    """Only web links and in-app paths are offered as a notification source."""
    if not value:
        return None
    text = value.strip()
    if text.startswith(("https://", "http://")) or (text.startswith("/") and not text.startswith("//")):
        return text
    return None


@router.get("/search")
def search(request: Request, q: str = ""):
    with SessionLocal() as db:
        ui_user(request, db)
        term = q.strip()[:200]
        found = core.search_records(db, term)
        return {
            "q": term,
            "customers": [{"id": str(c.id), "name": c.name, "code": c.code} for c in found["customers"]],
            "sites": [{"id": str(s.id), "name": s.name, "address": s.address,
                       "customer": {"id": str(s.customer.id), "name": s.customer.name} if s.customer else None} for s in found["sites"]],
            "devices": [{
                "id": str(d.id), "name": d.display_name or d.device_identity or d.name, "vendor": d.vendor, "model": d.model,
                "status": d.status, "brand": brand_icon(d), "serial_number": d.serial_number, "primary_mac": d.primary_mac,
                "management_ip": d.management_ip, "external_device_id": d.external_device_id,
                "customer": {"id": str(d.customer.id), "name": d.customer.name} if d.customer else None,
                "site": {"id": str(d.site.id), "name": d.site.name} if d.site else None,
            } for d in found["devices"]],
        }


def _unread_count(db, user) -> int:
    return int(db.scalar(select(func.count(Notification.id)).where(*core.unread_notification_conditions(user))) or 0)


@router.get("/notifications/summary")
def notifications_summary(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db)
        return {"unread": _unread_count(db, user)}


@router.get("/notifications")
def notifications(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db)
        rows = core.notification_rows(db, user)
        device_ids = {n.device_id for n, _ in rows if n.device_id}
        devices = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_(device_ids)))} if device_ids else {}
        return jsonable_encoder({
            "items": [{
                "id": str(n.id), "created_at": n.created_at, "severity": n.severity or "info", "category": n.category or "system",
                "title": n.title, "message": n.message, "source_url": _safe_url(n.source_url), "is_read": bool(is_read),
                "advisory_id": str(n.advisory_id) if n.advisory_id else None, "device": device_ref(devices.get(n.device_id)),
            } for n, is_read in rows],
            "unread": _unread_count(db, user),
        })


@router.post("/notifications/read-all", dependencies=[Depends(require_csrf)])
def notifications_read_all(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db)
        marked = core.mark_all_notifications_read_record(db, user)
        return {"marked": marked, "unread": _unread_count(db, user)}


@router.post("/notifications/{notification_id}/read", dependencies=[Depends(require_csrf)])
def notification_read(request: Request, notification_id: str):
    with SessionLocal() as db:
        user = ui_user(request, db)
        try:
            ident = uuid.UUID(notification_id)
        except ValueError:
            raise HTTPException(404) from None
        core.mark_notification_read_record(db, user, ident)
        return {"unread": _unread_count(db, user)}


def install_ui_personal(app) -> None:
    app.include_router(router)
