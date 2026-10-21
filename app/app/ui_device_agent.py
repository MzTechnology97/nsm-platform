"""Device Agent tab for the new interface (phase 1d), MikroTik only.

- ``GET  /api/ui/v1/devices/{id}/agent``: installation pipeline, heartbeat and
  transport, agent update state, effective capabilities, credential metadata
  (never the secret) and recent enrollments, built with the same helpers as
  the classic page;
- ``POST /api/ui/v1/devices/{id}/agent/update``: queues the self-update job
  (``devices.enroll``), same rules as the classic button.

- ``POST /api/ui/v1/devices/{id}/agent/reinstall``: new one-shot enrollment
  (``devices.enroll``, ``agent_ui.reinstall_record``); the answer carries the
  RouterOS command, shown once (``no-store``).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select

from app import agent_ui, mikrotik_onboarding
from app import main as core
from app import mikrotik_agent_status as agent_status
from app import mikrotik_agent_update as updater
from app.agent_models import DeviceAgentCredential
from app.backup_capabilities import capability_for_device
from app.db import SessionLocal
from app.mikrotik_workspace import _load_device
from app.models import DeviceEnrollment
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])


def _iso(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value or None


def _mikrotik(db, device_id):
    device = _load_device(db, device_id)
    if device.vendor != "mikrotik":
        raise HTTPException(404, "Agent disponibile solo per MikroTik.")
    return device


@router.get("/devices/{device_id}/agent")
def device_agent(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device = _mikrotik(db, device_id)
        credential = db.scalar(select(DeviceAgentCredential).where(DeviceAgentCredential.device_id == device.id,
                                                                   DeviceAgentCredential.agent_type == "mikrotik_agent"))
        enrollments = list(db.scalars(select(DeviceEnrollment).where(DeviceEnrollment.device_id == device.id)
                                      .order_by(DeviceEnrollment.created_at.desc()).limit(10)))
        inventory = dict(device.inventory_data or {})
        active = bool(credential and credential.is_active)
        installation = agent_status._installation_state(device, credential, enrollments)
        heartbeat = agent_status._heartbeat_state(device, inventory)
        capabilities = agent_status._agent_capabilities(device, credential, capability_for_device(db, device, active_agent=active))
        update = updater.agent_update_status(device)
        can_enroll = core.has_permission(user, "devices.enroll")
        return {
            "installation": {
                "key": installation["key"], "severity": installation["severity"], "paired_at": _iso(installation.get("paired_at")),
                "grace_minutes": installation.get("grace_minutes"),
                "steps": [{"key": step["key"], "done": bool(step["done"]),
                           "at": _iso(step["detail"]) if isinstance(step["detail"], datetime) else None,
                           "note": step["detail"] if isinstance(step["detail"], str) else None} for step in installation["steps"]],
            },
            "heartbeat": {
                "last_seen": _iso(heartbeat["last_seen"]), "stale": bool(heartbeat["stale"]), "source_ip": heartbeat["last_source_ip"],
                "transport": heartbeat["transport"], "heartbeat_transport": heartbeat["heartbeat_transport"],
                "enrollment_transport": heartbeat["enrollment_transport"], "agent_version": heartbeat["agent_version"],
            },
            "update": {
                "current_version": update.get("current_version"), "target_version": update.get("target_version"),
                "outdated": bool(update.get("outdated")), "self_update_capable": bool(update.get("self_update_capable")),
                "state": update.get("state") or "idle", "source_drift": bool(update.get("source_drift")),
                "transport_upgrade": bool(update.get("transport_upgrade")), "reinstall_reason": update.get("reinstall_reason"),
                "policies": list(update.get("policies") or []),
                "requested_at": update.get("requested_at"), "verified_at": update.get("verified_at"),
            },
            "capabilities": [{"key": c["key"], "label": c["label"], "available": bool(c["available"]), "detail": c.get("detail")} for c in capabilities],
            "credential": {"active": bool(credential.is_active), "created_at": _iso(credential.created_at), "last_used_at": _iso(credential.last_used_at),
                           "rotated_at": _iso(credential.rotated_at)} if credential else None,
            "enrollments": [{"created_at": _iso(e.created_at), "source": e.source, "status": e.status, "expires_at": _iso(e.expires_at),
                             "used_at": _iso(e.used_at)} for e in enrollments],
            "firmware": device.firmware_version,
            "actions": {
                "update": bool(update.get("outdated") and update.get("self_update_capable") and can_enroll),
                "reinstall": can_enroll,
            },
            "links": {"classic": f"/devices/{device.id}/agent", "reinstall": f"/devices/{device.id}/agent/reinstall"},
        }


@router.post("/devices/{device_id}/agent/update", dependencies=[Depends(require_csrf)])
def device_agent_update(request: Request, device_id: uuid.UUID):
    from app.mikrotik_agent_autoupdate import queue_update

    with SessionLocal() as db:
        user = ui_user(request, db, "devices.enroll")
        device = _mikrotik(db, device_id)
        try:
            job = queue_update(db, device, actor=user)
        except HTTPException as exc:
            raise HTTPException(exc.status_code, {"code": "refused", "message": str(exc.detail)}) from exc
        db.commit()
        return {"queued": True, "job_id": str(job.id), "target_version": (job.payload or {}).get("target_version")}


def install_ui_device_agent(app) -> None:
    app.include_router(router)


@router.post("/devices/{device_id}/agent/reinstall", dependencies=[Depends(require_csrf)])
def reinstall(request: Request, response: Response, device_id: uuid.UUID):
    response.headers["Cache-Control"] = "no-store"
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.enroll")
        device = _mikrotik(db, device_id)
        raw_token = agent_ui.reinstall_record(db, user, device)
        db.commit()
        return mikrotik_onboarding.enrollment_commands(str(request.base_url), raw_token)
