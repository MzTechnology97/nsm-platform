"""Counters next to the sidebar items of the new interface (``GET /api/ui/v1/nav-counts``).

The menu asks for them once a minute, so every figure is a single COUNT query (no dashboard computation). Each
figure is returned only when the user has the permission of the page it points to. The customer scope and the
decommissioned filter apply to these ORM queries as to every other one (``customer_scope``).

The monthly SLA verdicts are not counted here: they aggregate every ping round of the month, too heavy for a
request repeated by every open browser.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app import main as core
from app.customer_drilldown import _firmware_attention_condition, _severe_cve_exists
from app.db import SessionLocal
from app.models import ActionIssue, Device
from app.ui_api import ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
OPEN_ISSUE = ("open", "acknowledged")


def _count(db, model, *conditions) -> int:
    return int(db.scalar(select(func.count(model.id)).where(*conditions)) or 0)


def nav_counts(db, user) -> dict:
    def allowed(permission: str) -> bool:
        return core.has_permission(user, permission)

    counts: dict = {}
    if allowed("devices.read"):
        counts["devices"] = _count(db, Device)
    if allowed("monitoring.read"):
        counts["offline"] = _count(db, Device, Device.status == "offline")
    if allowed("security.read"):
        counts["severe_cve_devices"] = _count(db, Device, _severe_cve_exists())
    if allowed("firmware.read"):
        counts["firmware_attention"] = _count(db, Device, _firmware_attention_condition())
    if allowed("issues.read"):
        counts["open_issues"] = _count(db, ActionIssue, ActionIssue.status.in_(OPEN_ISSUE))
        counts["critical_issues"] = _count(db, ActionIssue, ActionIssue.status.in_(OPEN_ISSUE), ActionIssue.severity == "critical")
    return counts


@router.get("/nav-counts")
def nav_counts_endpoint(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db)
        return nav_counts(db, user)


def install_nav_counts(app) -> None:
    app.include_router(router)
