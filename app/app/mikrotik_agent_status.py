"""MikroTik agent diagnostics workspace for Core 0.34.

The page exposes operational metadata only. Secret hashes and raw agent secrets
are never rendered.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceAgentCredential
from app.backup_capabilities import capability_for_device
from app.db import SessionLocal
from app.mikrotik_workspace import _load_device, _workspace_context
from app.models import DeviceEnrollment, utcnow

router = APIRouter()
HEARTBEAT_STALE_AFTER = timedelta(minutes=15)


def _agent_capabilities(device, credential, backup_capability):
    inventory = dict(device.inventory_data or {})
    transport = str(inventory.get("agent_transport") or "unknown").lower()
    authenticated = bool(credential and credential.is_active)
    online = device.status == "online"
    modern = transport == "modern"
    legacy = transport == "legacy"
    return [
        {
            "key": "heartbeat",
            "label": "Heartbeat / inventario",
            "available": authenticated and (modern or legacy),
            "detail": "Header transport" if legacy else "JSON autenticato" if modern else "Trasporto non rilevato",
        },
        {
            "key": "diagnostics",
            "label": "Diagnostica read-only",
            "available": authenticated and (modern or legacy),
            "detail": "Allow-list plain-text" if legacy else "Job agent moderno",
        },
        {
            "key": "firmware",
            "label": "Firmware readiness",
            "available": authenticated and (modern or legacy),
            "detail": "Solo lettura",
        },
        {
            "key": "snapshots",
            "label": "Snapshot configurazione",
            "available": authenticated and modern,
            "detail": "Richiede agent moderno (RouterOS 7.13+)" if legacy else "Disponibile" if modern else "Transport non rilevato",
        },
        {
            "key": "backup",
            "label": "Backup HTTPS",
            "available": bool(backup_capability.executable),
            "detail": backup_capability.reason,
        },
        {
            "key": "telemetry",
            "label": "Telemetria storica",
            "available": authenticated and modern,
            "detail": "Il legacy invia metriche heartbeat ma non il transport storico completo" if legacy else "Retention 90 giorni" if modern else "Transport non rilevato",
        },
        {
            "key": "online",
            "label": "Operatività agent",
            "available": authenticated and online,
            "detail": "Online" if online else "Ultimo stato dispositivo offline",
        },
    ]


def _heartbeat_state(device, inventory):
    now = utcnow()
    last_seen = device.last_seen
    stale = not last_seen or (now - last_seen) > HEARTBEAT_STALE_AFTER
    return {
        "last_seen": last_seen,
        "stale": stale,
        "last_source_ip": inventory.get("last_source_ip"),
        "last_heartbeat_at": inventory.get("last_heartbeat_at"),
        "transport": inventory.get("agent_transport") or ("legacy" if inventory.get("legacy_agent") else "unknown"),
        "heartbeat_transport": inventory.get("legacy_heartbeat_transport") or ("json-v1" if inventory.get("agent_transport") == "modern" else None),
        "enrollment_transport": inventory.get("enrollment_transport") or "legacy-json-v1",
        "agent_version": inventory.get("agent_version"),
    }


@router.get("/devices/{device_id}/agent", response_class=HTMLResponse, name="mikrotik_agent_status")
def agent_status(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        device = _load_device(db, device_id)
        if device.vendor != "mikrotik":
            raise HTTPException(404)

        credential = db.scalar(
            select(DeviceAgentCredential).where(
                DeviceAgentCredential.device_id == device.id,
                DeviceAgentCredential.agent_type == "mikrotik_agent",
            )
        )
        enrollments = list(
            db.scalars(
                select(DeviceEnrollment)
                .where(DeviceEnrollment.device_id == device.id)
                .order_by(DeviceEnrollment.created_at.desc())
                .limit(10)
            )
        )
        inventory = dict(device.inventory_data or {})
        backup_capability = capability_for_device(
            db,
            device,
            active_agent=bool(credential and credential.is_active),
        )
        ctx = _workspace_context(db, device)
        ctx.update(
            {
                "agent_credential": credential,
                "agent_enrollments": enrollments,
                "agent_heartbeat": _heartbeat_state(device, inventory),
                "agent_capabilities": _agent_capabilities(device, credential, backup_capability),
                "agent_backup_capability": backup_capability,
            }
        )
        return core.render(
            request,
            db,
            user,
            "mikrotik_workspace.html",
            device=device,
            active_tab="agent",
            **ctx,
        )


def install_mikrotik_agent_status(app):
    app.include_router(router)
