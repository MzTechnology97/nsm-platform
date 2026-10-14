"""Action Center and incidents for the new interface (phase 5).

- ``GET  /api/ui/v1/action-center``: open (or chosen) issues, most severe
  first, with category and severity counts, the same filters as the classic
  page (``customer_drilldown.action_center``);
- ``POST /api/ui/v1/action-center/{issue_id}/ack``: take an issue in charge
  (``issues.ack``), audited like the classic button.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import case, func, select

from app import main as core
from app.db import SessionLocal
from app.models import ActionIssue, Customer, Device
from app.ui_api import brand_icon, require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
ISSUE_PAGE = 100
OPEN_STATES = ("open", "acknowledged")
SEVERITY_ORDER = case(
    (ActionIssue.severity == "critical", 1),
    (ActionIssue.severity == "high", 2),
    (ActionIssue.severity.in_(["warning", "medium"]), 3),
    (ActionIssue.severity == "low", 4),
    else_=5,
)


def _iso(value):
    return value.isoformat() if value else None


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def device_ref(device) -> dict | None:
    if device is None:
        return None
    return {"id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device),
            "vendor": device.vendor, "status": device.status}


@router.get("/action-center")
def action_center(request: Request, severity: str = "", category: str = "", status: str = "", customer: str = "", offset: int = 0):
    with SessionLocal() as db:
        user = ui_user(request, db, "issues.read")
        customer_id = _uuid(customer)
        base = [ActionIssue.status == status if status else ActionIssue.status.in_(OPEN_STATES)]
        if customer_id:
            base.append(ActionIssue.customer_id == customer_id)
        # Severity chips count within state and customer; category chips also within the chosen severity.
        severities = {name or "info": int(count) for name, count in db.execute(
            select(ActionIssue.severity, func.count(ActionIssue.id)).where(*base).group_by(ActionIssue.severity)).all()}
        filters = [*base, *([ActionIssue.severity == severity] if severity else [])]
        categories = [{"key": name or "system", "count": int(count)} for name, count in db.execute(
            select(ActionIssue.category, func.count(ActionIssue.id)).where(*filters)
            .group_by(ActionIssue.category).order_by(func.count(ActionIssue.id).desc(), ActionIssue.category)).all()]
        if category:
            filters.append(ActionIssue.category == category)
        total = int(db.scalar(select(func.count(ActionIssue.id)).where(*filters)) or 0)
        offset = max(0, int(offset or 0))
        issues = list(db.scalars(select(ActionIssue).where(*filters).order_by(SEVERITY_ORDER, ActionIssue.created_at.desc())
                                 .offset(offset).limit(ISSUE_PAGE)))
        devices = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_({i.device_id for i in issues if i.device_id})))} if issues else {}
        customers = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_({i.customer_id for i in issues if i.customer_id})))} if issues else {}
        return {
            "total": total, "offset": offset, "limit": ISSUE_PAGE, "scope_total": sum(c["count"] for c in categories),
            "categories": categories, "severities": severities,
            "can_ack": core.has_permission(user, "issues.ack"),
            "can_incident": core.has_permission(user, "incidents.write"),
            "items": [{
                "id": str(i.id), "title": i.title, "severity": i.severity, "category": i.category or "system", "status": i.status,
                "created_at": _iso(i.created_at), "updated_at": _iso(i.updated_at),
                "device": device_ref(devices.get(i.device_id)),
                "customer": {"id": str(i.customer_id), "name": customers[i.customer_id].name} if i.customer_id in customers else None,
            } for i in issues],
        }


@router.post("/action-center/{issue_id}/ack", dependencies=[Depends(require_csrf)])
def action_center_ack(request: Request, issue_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "issues.ack")
        issue = db.get(ActionIssue, issue_id)
        if not issue:
            raise HTTPException(404, "Segnalazione non trovata.")
        changed = core.acknowledge_issue_record(db, issue, user)
        db.commit()
        return {"acknowledged": changed, "status": issue.status}


def install_ui_incidents(app) -> None:
    app.include_router(router)
