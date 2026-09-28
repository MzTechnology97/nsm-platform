"""MikroTik agent diagnostics and install-verification workspace.

The page exposes operational metadata only. Secret hashes and raw agent secrets
are never rendered. Core 0.41 adds an explicit verification state machine so a
real-device onboarding test can distinguish token creation, pairing, credential
activation and first post-pairing heartbeat.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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
PAIRING_HEARTBEAT_GRACE = timedelta(minutes=5)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _transport(inventory: dict) -> str:
    explicit = str(inventory.get("agent_transport") or "").strip().lower()
    if explicit in {"modern", "legacy"}:
        return explicit
    version = str(inventory.get("agent_version") or "").strip().lower()
    if inventory.get("legacy_agent") or version.endswith("-legacy"):
        return "legacy"
    if version:
        return "modern"
    return "unknown"


def _agent_capabilities(device, credential, backup_capability):
    inventory = dict(device.inventory_data or {})
    transport = _transport(inventory)
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
    last_seen = _aware(device.last_seen)
    stale = not last_seen or (now - last_seen) > HEARTBEAT_STALE_AFTER
    transport = _transport(inventory)
    enrollment_transport = inventory.get("enrollment_transport")
    if not enrollment_transport:
        enrollment_transport = "json-v1" if inventory.get("agent_version") else "unknown"
    heartbeat_transport = inventory.get("legacy_heartbeat_transport")
    if not heartbeat_transport and transport == "modern":
        heartbeat_transport = "json-v1"
    return {
        "last_seen": last_seen,
        "stale": stale,
        "last_source_ip": inventory.get("last_source_ip"),
        "last_heartbeat_at": inventory.get("last_heartbeat_at"),
        "transport": transport,
        "heartbeat_transport": heartbeat_transport,
        "enrollment_transport": enrollment_transport,
        "agent_version": inventory.get("agent_version"),
    }


def _installation_state(device, credential, enrollments):
    """Return a deterministic onboarding/installation verification state.

    Pairing and script execution are intentionally separate: consuming a token
    proves that RouterOS reached the enrollment endpoint, while a heartbeat
    after ``used_at`` proves that the installed script parsed and ran.
    """
    now = utcnow()
    latest = enrollments[0] if enrollments else None
    used = next((item for item in enrollments if item.used_at), None)
    used_at = _aware(used.used_at) if used else None
    last_seen = _aware(device.last_seen)
    credential_active = bool(credential and credential.is_active)
    token_pending = bool(
        latest
        and latest.status == "pending"
        and _aware(latest.expires_at)
        and _aware(latest.expires_at) > now
    )
    token_expired = bool(
        latest
        and latest.status == "pending"
        and _aware(latest.expires_at)
        and _aware(latest.expires_at) <= now
    )
    heartbeat_after_pairing = bool(
        credential_active
        and last_seen
        and (not used_at or last_seen >= used_at)
    )

    if heartbeat_after_pairing:
        if (now - last_seen) > HEARTBEAT_STALE_AFTER:
            key, label, severity = "stale", "Agent installato · heartbeat stale", "warning"
            detail = "Il pairing è verificato, ma il dispositivo non invia heartbeat recente."
        else:
            key, label, severity = "verified", "Agent verificato", "success"
            detail = "Pairing, credenziale e heartbeat post-installazione sono stati verificati."
    elif used_at and credential_active:
        elapsed = now - used_at
        if elapsed <= PAIRING_HEARTBEAT_GRACE:
            key, label, severity = "awaiting_heartbeat", "Pairing completato · attesa heartbeat", "info"
            detail = "La credenziale è stata emessa. Attendo il primo heartbeat dell'agent installato."
        else:
            key, label, severity = "install_suspect", "Pairing OK · agent non avviato", "critical"
            detail = (
                "Il token è stato consumato e la credenziale esiste, ma non è arrivato un heartbeat "
                "post-pairing. Verificare parser RouterOS, scheduler e log del dispositivo."
            )
    elif token_pending:
        key, label, severity = "awaiting_pairing", "In attesa di pairing", "info"
        detail = "Token one-shot valido; il router non lo ha ancora consumato."
    elif token_expired:
        key, label, severity = "token_expired", "Token scaduto", "warning"
        detail = "Generare un nuovo comando di onboarding e ripetere il preflight sul MikroTik."
    elif credential_active and last_seen:
        # Covers credentials created before enrollment history was retained.
        key, label, severity = "verified_legacy_record", "Agent operativo", "success"
        detail = "Heartbeat e credenziale sono validi; lo storico pairing originario non è disponibile."
    elif credential_active:
        key, label, severity = "credential_no_heartbeat", "Credenziale attiva · nessun heartbeat", "critical"
        detail = "La credenziale esiste ma non è mai stato osservato un heartbeat dell'agent."
    else:
        key, label, severity = "not_enrolled", "Agent non associato", "warning"
        detail = "Nessuna credenziale agent attiva. Avviare o rigenerare l'onboarding."

    steps = [
        {
            "key": "token",
            "label": "Token creato",
            "done": bool(latest),
            "detail": latest.status if latest else "nessun enrollment",
        },
        {
            "key": "pairing",
            "label": "Pairing one-shot",
            "done": bool(used_at),
            "detail": used_at,
        },
        {
            "key": "credential",
            "label": "Credenziale per-device",
            "done": credential_active,
            "detail": credential.last_used_at if credential else None,
        },
        {
            "key": "heartbeat",
            "label": "Heartbeat post-installazione",
            "done": heartbeat_after_pairing,
            "detail": last_seen,
        },
    ]
    return {
        "key": key,
        "label": label,
        "severity": severity,
        "detail": detail,
        "steps": steps,
        "paired_at": used_at,
        "heartbeat_after_pairing": heartbeat_after_pairing,
        "grace_minutes": int(PAIRING_HEARTBEAT_GRACE.total_seconds() // 60),
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
                "agent_installation": _installation_state(device, credential, enrollments),
                "agent_capabilities": _agent_capabilities(device, credential, backup_capability),
                "agent_backup_capability": backup_capability,
            }
        )
        return core.render(
            request,
            db,
            user,
            "mikrotik_agent_status.html",
            device=device,
            active_tab="agent",
            **ctx,
        )


def install_mikrotik_agent_status(app):
    app.include_router(router)
