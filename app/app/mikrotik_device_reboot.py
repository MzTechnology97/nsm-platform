"""Operator-requested MikroTik reboot with post-reboot verification.

Workflow: operator request (typed confirmation and reason) -> the Agent
acknowledges the job to NSM and only then runs ``/system reboot`` -> NSM marks
the reboot verified when a later heartbeat reports an uptime shorter than the
time elapsed since the acknowledgement.  No heartbeat within the verification
window leaves an explicit failure.  The server never sends commands: the
handler is compiled into the Agent and only takes the job id.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent_module
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.mikrotik_privilege_profile import OPERATIONAL_PROFILES
from app.models import Device, utcnow
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
REBOOT_JOB = "device_reboot"
STATE_KEY = "device_reboot"
CONFIRMATION = "RIAVVIA"
JOB_TTL = timedelta(minutes=10)
VERIFY_TIMEOUT = timedelta(minutes=15)
# Jobs that already reboot the Device or must not be interrupted by a reboot.
CONFLICTING_JOBS = {
    REBOOT_JOB, "routerboot_stage", "routerboot_reboot", "firmware_stage", "firmware_activate",
    "agent_self_update", "backup_mikrotik",
}
STATUS_LABELS = {
    "queued": "In coda: l'agent lo riceverà al prossimo heartbeat",
    "rebooting": "Riavvio confermato dall'agent, in attesa del ritorno online",
    "verified": "Riavvio verificato",
    "failed": "Riavvio non riuscito",
}

_HANDLER = r'''
    :if ($nsmJobType = "device_reboot") do={
      :local nsmAckUrl ($nsmBase . "/api/v1/agents/mikrotik/device-reboot/" . $nsmJobId . "/ack")
      :local nsmAckBody [:serialize value={"status"="accepted";"result"={"uptime"=[:tostr [/system resource get uptime]]}} to=json options=json.no-string-conversion]
      :local nsmAckOk false
      :do {
        :local nsmAckResult [/tool fetch url=$nsmAckUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmAckBody output=user as-value]
        :if (($nsmAckResult->"status") = "finished") do={ :set nsmAckOk true }
      } on-error={ :log warning "NSM reboot acknowledgement failed; reboot cancelled" }
      :if ($nsmAckOk) do={
        :log warning "NSM requested reboot: restarting in 3 seconds"
        :delay 3s
        /system reboot
      }
    }
'''

_UPTIME_PART = re.compile(r"(\d+)([wdhms])")
_UNIT_SECONDS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_uptime(value) -> int | None:
    """Seconds from RouterOS uptime: ``1w2d03:04:05``, ``2d1h3m4s``, ``00:05:12``."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    seconds = 0
    clock = re.search(r"(\d+):(\d{2}):(\d{2})$", text)
    if clock:
        seconds += int(clock.group(1)) * 3600 + int(clock.group(2)) * 60 + int(clock.group(3))
        text = text[: clock.start()]
    parts = _UPTIME_PART.findall(text)
    if not parts and not clock:
        return None
    for number, unit in parts:
        seconds += int(number) * _UNIT_SECONDS[unit]
    return seconds


def _as_utc(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _state(device: Device) -> dict:
    return dict((device.inventory_data or {}).get(STATE_KEY) or {})


def _write_state(device: Device, **changes) -> dict:
    data = dict(device.inventory_data or {})
    state = dict(data.get(STATE_KEY) or {})
    state.update(changes)
    data[STATE_KEY] = state
    device.inventory_data = data
    return state


def reboot_blocker(db, device: Device) -> str | None:
    """Reason why NSM cannot ask this Device to reboot, or None."""
    data = device.inventory_data or {}
    if device.vendor != "mikrotik":
        return "Il riavvio remoto è disponibile solo per MikroTik."
    if str(data.get("agent_transport") or "") != "modern":
        return "Il riavvio remoto richiede l'agent moderno (RouterOS 7.13+); l'agent legacy ha solo permessi di lettura."
    if str(data.get("agent_privilege_profile") or "") not in OPERATIONAL_PROFILES:
        return "Il profilo privilegi dell'agent non include «reboot»: reinstalla l'agent."
    active = db.scalar(
        select(DeviceAgentCredential.id).where(
            DeviceAgentCredential.device_id == device.id,
            DeviceAgentCredential.agent_type == "mikrotik_agent",
            DeviceAgentCredential.is_active.is_(True),
        )
    )
    if not active:
        return "Nessuna credenziale agent attiva."
    if device.status != "online":
        return "L'apparato deve essere online."
    busy = db.scalar(
        select(DeviceJob.job_type).where(
            DeviceJob.device_id == device.id,
            DeviceJob.job_type.in_(sorted(CONFLICTING_JOBS)),
            DeviceJob.status.in_(["pending", "delivered", "running"]),
        )
    )
    if busy:
        return f"Operazione in corso sull'apparato ({busy}): attendi che termini."
    return None


def _load(db, device_id) -> Device:
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Apparato non trovato.")
    return device


@router.get("/devices/{device_id}/reboot", response_class=HTMLResponse, name="device_reboot_page")
def reboot_page(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "firmware.read"):
            raise HTTPException(403)
        device = _load(db, device_id)
        history = list(
            db.scalars(
                select(DeviceJob)
                .where(DeviceJob.device_id == device.id, DeviceJob.job_type == REBOOT_JOB)
                .order_by(DeviceJob.created_at.desc())
                .limit(10)
            )
        )
        return core.render(
            request, db, user, "device_reboot.html", title="Riavvio",
            device=device, blocker=reboot_blocker(db, device), state=_state(device),
            status_labels=STATUS_LABELS, history=history, confirmation=CONFIRMATION,
            can_execute=core.has_permission(user, "firmware.execute"),
        )


@router.post("/devices/{device_id}/reboot", name="queue_device_reboot")
async def queue_reboot(request: Request, device_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    back = f"/devices/{device_id}/reboot"
    with SessionLocal() as db:
        user = core.require_permission(request, db, "firmware.execute")
        device = _load(db, device_id)
        blocker = reboot_blocker(db, device)
        if blocker:
            return flash_redirect(request, back, "warning", blocker, title="Riavvio non disponibile")
        if str(form.get("confirmation") or "").strip() != CONFIRMATION:
            return flash_redirect(request, back, "warning", f"Per confermare scrivi esattamente {CONFIRMATION}.", title="Conferma mancante")
        reason = str(form.get("reason") or "").strip()
        if len(reason) < 5:
            return flash_redirect(request, back, "warning", "Indica il motivo del riavvio (almeno 5 caratteri).", title="Motivo mancante")
        now = utcnow()
        job = DeviceJob(device_id=device.id, job_type=REBOOT_JOB, payload={"reason": reason[:500]}, expires_at=now + JOB_TTL)
        db.add(job)
        db.flush()
        _write_state(device, status="queued", job_id=str(job.id), queued_at=now.isoformat(), reason=reason[:500], requested_by=user.username, accepted_at=None, verified_at=None, last_error=None)
        core.add_event(
            db, "DEVICE_REBOOT_QUEUED", actor=user, customer_id=device.customer_id, device_id=device.id,
            details={"job_id": str(job.id), "reason": reason[:500]}, severity="warning", source="portal",
        )
        db.commit()
    return flash_redirect(request, back, "success", "Riavvio in coda: l'agent lo eseguirà al prossimo heartbeat (entro 2 minuti).", title="Riavvio richiesto")


@router.post("/api/v1/agents/mikrotik/device-reboot/{job_id}/ack", name="mikrotik_device_reboot_ack")
async def reboot_ack(request: Request, job_id: uuid.UUID):
    payload = await agent_module._json_body(request)
    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type != REBOOT_JOB:
            raise HTTPException(404, "Job di riavvio non trovato.")
        if job.status not in {"delivered", "running"}:
            # Expired or already handled: the Agent must not reboot.
            raise HTTPException(409, "Job di riavvio non più valido.")
        now = utcnow()
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        job.status = "running"
        job.result = {"uptime_before": str(result.get("uptime") or "")[:80]}
        _write_state(device, status="rebooting", accepted_at=now.isoformat(), uptime_before=job.result["uptime_before"])
        core.add_event(
            db, "DEVICE_REBOOT_ACCEPTED", customer_id=device.customer_id, device_id=device.id,
            details={"job_id": str(job.id), "uptime_before": job.result["uptime_before"]}, severity="warning", source="mikrotik_agent",
        )
        db.commit()
    return {"status": "ok"}


def verify_reboots(now=None) -> dict:
    """Worker tick: close reboots by uptime evidence or by timeout."""
    now = now or utcnow()
    stats = {"verified": 0, "failed": 0}
    with SessionLocal() as db:
        for device in db.scalars(select(Device).where(Device.vendor == "mikrotik")):
            state = _state(device)
            if state.get("status") not in {"queued", "rebooting"}:
                continue
            job = db.get(DeviceJob, uuid.UUID(state["job_id"])) if state.get("job_id") else None
            if state["status"] == "queued":
                if job is None or job.status == "failed":
                    error = "L'agent non ha preso in carico il riavvio entro 10 minuti: nessun riavvio effettuato."
                    _write_state(device, status="failed", last_error=error, failed_at=now.isoformat())
                    stats["failed"] += 1
                    core.add_event(db, "DEVICE_REBOOT_FAILED", customer_id=device.customer_id, device_id=device.id, details={"reason": "not_acknowledged"}, severity="warning", result="failed", source="worker")
                continue
            accepted = _as_utc(state.get("accepted_at"))
            seen = _as_utc(device.last_seen)
            uptime = parse_uptime((device.inventory_data or {}).get("uptime"))
            if accepted and seen and seen > accepted and uptime is not None and uptime < (seen - accepted).total_seconds():
                _write_state(device, status="verified", verified_at=now.isoformat(), uptime_after=(device.inventory_data or {}).get("uptime"), last_error=None)
                if job:
                    job.status = "success"
                    job.completed_at = now
                    job.result = {**(job.result or {}), "uptime_after": (device.inventory_data or {}).get("uptime")}
                stats["verified"] += 1
                core.add_event(db, "DEVICE_REBOOT_VERIFIED", customer_id=device.customer_id, device_id=device.id, details={"uptime_after": (device.inventory_data or {}).get("uptime")}, source="worker")
            elif accepted and now - accepted > VERIFY_TIMEOUT:
                error = f"Nessun heartbeat con uptime azzerato entro {int(VERIFY_TIMEOUT.total_seconds() // 60)} minuti dal riavvio: verifica l'apparato."
                _write_state(device, status="failed", last_error=error, failed_at=now.isoformat())
                if job:
                    job.status = "failed"
                    job.last_error = error
                    job.completed_at = now
                stats["failed"] += 1
                core.add_event(db, "DEVICE_REBOOT_FAILED", customer_id=device.customer_id, device_id=device.id, details={"reason": "not_verified"}, severity="error", result="failed", source="worker")
        db.commit()
    return stats


def _install_agent_handler():
    previous = agent_module._agent_source

    def source_with_reboot(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        marker = '    :if ($nsmJobType = "backup_mikrotik") do={'
        if marker not in source:
            raise RuntimeError("MikroTik reboot extension point not found")
        handler = _HANDLER.replace("output=user as-value]", "output=user as-value check-certificate=yes]") if check_certificate else _HANDLER
        return source.replace(marker, handler + "\n" + marker, 1)

    agent_module._agent_source = source_with_reboot


def install_mikrotik_device_reboot(app) -> None:
    _install_agent_handler()
    app.include_router(router)
