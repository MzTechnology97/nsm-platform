"""Device Syslog tab for the new interface (phase 1d).

- ``GET  /api/ui/v1/devices/{id}/syslog``: today's counters, last line,
  device logins (24 h), receiver state, how the device is recognised and the
  last agent configuration job.  The lines themselves come from the existing
  ``/api/v1/devices/{id}/logs`` (live tail by ``after_id``, older by ``before_id``).
- ``GET  /api/ui/v1/devices/{id}/syslog/integrity``: verifies the hash chain.
- ``POST /api/ui/v1/devices/{id}/syslog/configure``: queues the agent job, with
  the same rules as the classic form (``devices.write``, eligibility, NSM address).

Reading never creates the device syslog key: the manual setup stays on the
classic page, which generates it on first visit.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from app import main as core
from app import mikrotik_syslog_config as syslog_config
from app import device_logs, syslog_guides, syslog_integrity, syslog_receiver, syslog_security, vendor_cpe
from app.db import SessionLocal
from app.device_logs import device_identification
from app.models import Device, utcnow
from app.syslog_models import DeviceLogEntry
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])


def _iso(value):
    return value.isoformat() if value else None


def _device(db, device_id) -> Device:
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Apparato non trovato.")
    return device


def blocker_code(text: str | None) -> str | None:
    """Stable code for the eligibility message, so the interface can translate it."""
    if not text:
        return None
    if "solo con l'agent MikroTik" in text:
        return "vendor"
    if "sola lettura" in text:
        return "legacy_readonly"
    if "agent legacy" in text.lower() and "Serve" in text:
        return "legacy_version"
    if text.startswith("Agent legacy"):
        return "legacy"
    if text.startswith("Serve l'agent"):
        return "agent_version"
    return "other"


def _job(job) -> dict | None:
    if job is None:
        return None
    result = job.result if isinstance(job.result, dict) else {}
    return {"status": job.status, "created_at": _iso(job.created_at), "remote": (job.payload or {}).get("remote"),
            "error": job.last_error or result.get("error")}


@router.get("/devices/{device_id}/syslog")
def device_syslog(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "monitoring.read")
        device = _device(db, device_id)
        # Opening the Syslog tab creates the device key, so the manual guide shows the real prefix (as the classic page did).
        if not (device.inventory_data or {}).get("syslog_key"):
            syslog_config.identity.device_key(device)
            db.commit()
        since = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        counts = dict(db.execute(select(DeviceLogEntry.severity, func.count())
                                 .where(DeviceLogEntry.device_id == device.id, DeviceLogEntry.received_at >= since)
                                 .group_by(DeviceLogEntry.severity)).all())
        last = db.scalar(select(DeviceLogEntry).where(DeviceLogEntry.device_id == device.id).order_by(DeviceLogEntry.id.desc()).limit(1))
        access = syslog_security.access_summary(db, device.id)
        status = syslog_receiver.receiver_status()
        settings = syslog_receiver.load_settings(db)
        ident = device_identification(device, settings)
        blocker = syslog_config.eligibility(device) if device.vendor == "mikrotik" else None
        data = device.inventory_data or {}
        return {
            "today": {
                "errors": sum(counts.get(level, 0) for level in (0, 1, 2, 3)),
                "warnings": counts.get(4, 0),
                "other": sum(counts.get(level, 0) for level in (5, 6, 7)),
                "since": _iso(since),
            },
            "last": {"received_at": _iso(last.received_at), "source_ip": last.source_ip} if last else None,
            "access": {
                "failures": access["failures"], "successes": access["successes"],
                "top_sources": [{"ip": s["ip"], "count": s["count"], "last": _iso(s["last"]), "public": bool(s["public"])} for s in access["top_sources"]],
                "recent": [{"occurred_at": _iso(e.occurred_at), "outcome": e.outcome, "username": e.username, "remote_ip": e.remote_ip, "service": e.service}
                           for e in access["recent"]],
            },
            "receiver": {"alive": bool(status and status.get("alive"))},
            "identification": {"key_active": bool(ident["key_active"]), "agent": bool(ident["agent"]), "manual": bool(ident["manual"]),
                               "has_key": bool(ident["has_key"]),
                               "key": f"{syslog_config.identity.KEY_PREFIX}{data['syslog_key']}" if data.get("syslog_key") else None},
            "agent": None if device.vendor != "mikrotik" else {
                "blocker": blocker_code(blocker), "blocker_text": blocker,
                "job": _job(syslog_config.latest_job(db, device.id)),
                "can_configure": blocker is None and core.has_permission(user, "devices.write"),
            },
            "target": device_logs.public_host(db, request),
            "guides": syslog_guides.guides(vendor_cpe.brand(device) or "generic", device_logs.public_host(db, request),
                                           f"{syslog_config.identity.KEY_PREFIX}{data['syslog_key']}" if data.get("syslog_key") else None),
            "csv_url": f"/devices/{device.id}/logs.csv",
        }


@router.get("/devices/{device_id}/syslog/integrity")
def device_syslog_integrity(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        ui_user(request, db, "monitoring.read")
        device = _device(db, device_id)
        result = syslog_integrity.verify(db, device.id)
        return {"ok": bool(result.get("ok")), "checked": int(result.get("checked") or 0), "broken_at": result.get("broken_at"),
                "received_at": _iso(result.get("received_at")), "anchored_at": _iso(result.get("anchored_at")),
                "anchor_found": result.get("anchor_found")}


@router.post("/devices/{device_id}/syslog/configure", dependencies=[Depends(require_csrf)])
def device_syslog_configure(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _device(db, device_id)
        blocker = syslog_config.eligibility(device)
        if blocker:
            raise HTTPException(409, {"code": blocker_code(blocker), "message": blocker})
        remote = syslog_config.target(db, request)
        if not remote:
            raise HTTPException(409, {"code": "no_target", "message": "Indirizzo IPv4 di NSM non impostato in Amministrazione → Syslog."})
        job = syslog_config.queue(db, device, remote, actor=user)
        db.commit()
        if job is None:
            return {"queued": False, "already": True, "remote": remote}
        return {"queued": True, "already": False, "remote": remote, "job": _job(job)}


def install_ui_device_syslog(app) -> None:
    app.include_router(router)
