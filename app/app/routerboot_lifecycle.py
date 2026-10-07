"""Core 0.38 — explicit RouterBOOT lifecycle for MikroTik devices.

RouterBOOT is intentionally separate from RouterOS upgrades.  The workflow is:
readiness -> explicit stage -> explicit reboot -> post-reboot readiness verify.
No auto-upgrade RouterBOARD setting is enabled and no arbitrary commands are
accepted from the server.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent_module
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.models import Device, utcnow

router = APIRouter()
STAGE_JOB = "routerboot_stage"
REBOOT_JOB = "routerboot_reboot"
STATE_KEY = "routerboot_lifecycle"
OPERATIONAL_PROFILES = frozenset({"ops-v1", "ops-v2"})
MAX_RESULT_BODY = 64 * 1024
VERIFY_TIMEOUT = timedelta(minutes=15)
VERIFY_GRACE = timedelta(seconds=30)
STAGE_LOST_ERROR = "Flash RouterBOOT non eseguito dall'agent entro la finestra prevista: nessuna modifica confermata, ripetere lo staging."
REBOOT_LOST_ERROR = "Reboot non eseguito dall'agent entro la finestra prevista: nessun riavvio effettuato, ripetere il reboot."
VERIFY_NO_DATA_ERROR = "Nessuna lettura RouterBOOT ricevuta dopo il reboot entro il tempo di verifica."

_STAGE_HANDLER = r'''
    :if ($nsmJobType = "routerboot_stage") do={
      :local nsmPayload ($nsmJob->"payload")
      :local nsmTarget ($nsmPayload->"target_version")
      :local nsmOk true
      :local nsmError ""
      :local nsmCurrent ""
      :local nsmUpgrade ""
      :do {
        :set nsmCurrent [/system routerboard get current-firmware]
        :set nsmUpgrade [/system routerboard get upgrade-firmware]
        :if ($nsmCurrent = $nsmTarget) do={ :error "NSM RouterBOOT target already active" }
        :if ($nsmUpgrade != $nsmTarget) do={ :error "NSM RouterBOOT target changed" }
        /system routerboard upgrade
        :delay 2s
      } on-error={
        :set nsmOk false
        :set nsmError "RouterBOOT stage failed or target changed"
      }
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/routerboot-stage/" . $nsmJobId . "/complete")
      :local nsmStatus "failed"
      :if ($nsmOk) do={ :set nsmStatus "success" }
      :local nsmBody [:serialize value={"status"=$nsmStatus;"error"=$nsmError;"result"={"current_firmware"=$nsmCurrent;"target_version"=$nsmTarget;"upgrade_firmware"=$nsmUpgrade;"reboot_required"=$nsmOk}} to=json options=json.no-string-conversion]
      :do { /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmBody output=user as-value } on-error={ :log warning "NSM RouterBOOT stage result upload failed" }
    }
'''

_REBOOT_HANDLER = r'''
    :if ($nsmJobType = "routerboot_reboot") do={
      :local nsmPayload ($nsmJob->"payload")
      :local nsmTarget ($nsmPayload->"target_version")
      :local nsmOk true
      :local nsmError ""
      :local nsmCurrent ""
      :local nsmUpgrade ""
      :do {
        :set nsmCurrent [/system routerboard get current-firmware]
        :set nsmUpgrade [/system routerboard get upgrade-firmware]
        :if ($nsmCurrent = $nsmTarget) do={ :error "NSM RouterBOOT target already active" }
        :if ($nsmUpgrade != $nsmTarget) do={ :error "NSM RouterBOOT target changed before reboot" }
      } on-error={
        :set nsmOk false
        :set nsmError "RouterBOOT reboot preflight failed or target changed"
      }
      :local nsmAckUrl ($nsmBase . "/api/v1/agents/mikrotik/routerboot-reboot/" . $nsmJobId . "/ack")
      :local nsmAckStatus "failed"
      :if ($nsmOk) do={ :set nsmAckStatus "accepted" }
      :local nsmAckBody [:serialize value={"status"=$nsmAckStatus;"error"=$nsmError;"result"={"current_firmware"=$nsmCurrent;"target_version"=$nsmTarget;"upgrade_firmware"=$nsmUpgrade}} to=json options=json.no-string-conversion]
      :local nsmAckResult {}
      :do {
        :set nsmAckResult [/tool fetch url=$nsmAckUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmAckBody output=user as-value]
      } on-error={
        :set nsmOk false
        :log warning "NSM RouterBOOT reboot acknowledgement failed; reboot cancelled"
      }
      :if ($nsmOk) do={
        :if (($nsmAckResult->"status") = "finished") do={
          :delay 2s
          /system reboot
        } else={
          :log warning "NSM RouterBOOT reboot acknowledgement not accepted; reboot cancelled"
        }
      }
    }
'''


def _install_agent_handlers():
    previous = agent_module._agent_source

    def source_with_routerboot(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        marker = '    :if ($nsmJobType = "firmware_activate") do={'
        if marker not in source:
            marker = '    :if ($nsmJobType = "backup_mikrotik") do={'
        if marker not in source:
            raise RuntimeError("MikroTik RouterBOOT extension point not found")
        return source.replace(marker, _STAGE_HANDLER + "\n" + _REBOOT_HANDLER + "\n" + marker, 1)

    agent_module._agent_source = source_with_routerboot


def _active_agent(db, device_id):
    return db.scalar(
        select(DeviceAgentCredential.id).where(
            DeviceAgentCredential.device_id == device_id,
            DeviceAgentCredential.agent_type == "mikrotik_agent",
            DeviceAgentCredential.is_active.is_(True),
        )
    ) is not None


def _state(device: Device) -> dict:
    return dict((device.inventory_data or {}).get(STATE_KEY) or {})


def _readiness(device: Device) -> dict:
    return dict((device.inventory_data or {}).get("firmware_readiness") or {})


def _write_state(device: Device, **changes) -> dict:
    data = dict(device.inventory_data or {})
    state = dict(data.get(STATE_KEY) or {})
    state.update(changes)
    data[STATE_KEY] = state
    device.inventory_data = data
    return state


def _as_utc(value: str | datetime | None):
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _load_device(db, device_id):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Apparato non trovato.")
    if device.vendor != "mikrotik":
        raise HTTPException(400, "RouterBOOT disponibile solo per MikroTik.")
    return device


def _validate_modern_executor(db, device: Device):
    data = device.inventory_data or {}
    if str(data.get("agent_transport") or "").strip() != "modern":
        raise HTTPException(409, "RouterBOOT remoto richiede agent moderno RouterOS 7.13+.")
    if str(data.get("agent_privilege_profile") or "").strip() not in OPERATIONAL_PROFILES:
        raise HTTPException(409, "Profilo agent non idoneo. Rigenera / reinstalla agent prima di continuare.")
    if not _active_agent(db, device.id) or device.status != "online":
        raise HTTPException(409, "Il MikroTik deve essere online con credenziale agent attiva.")


def _target_from_readiness(device: Device):
    readiness = _readiness(device)
    current = str(readiness.get("routerboard_current") or device.routerboot_version or "").strip()
    target = str(readiness.get("routerboard_upgrade") or "").strip()
    if not current or not target:
        raise HTTPException(409, "Esegui prima una verifica firmware per leggere RouterBOOT current/upgrade.")
    return current, target


def _pending_job(db, device_id, types):
    return db.scalar(
        select(DeviceJob.id).where(
            DeviceJob.device_id == device_id,
            DeviceJob.job_type.in_(list(types)),
            DeviceJob.status.in_(["pending", "delivered", "running"]),
        )
    )


@router.get("/devices/{device_id}/routerboot", response_class=HTMLResponse, name="routerboot_lifecycle_page")
def routerboot_page(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.require_permission(request, db, "firmware.read")
        device = _load_device(db, device_id)
        state = _state(device)
        readiness = _readiness(device)
        current = readiness.get("routerboard_current") or device.routerboot_version
        target = readiness.get("routerboard_upgrade")
        can_execute = core.has_permission(user, "firmware.execute")
        response = core.templates.TemplateResponse(
            request=request,
            name="routerboot_lifecycle.html",
            context={
                "user": user,
                "device": device,
                "state": state,
                "readiness": readiness,
                "current": current,
                "target": target,
                "can_execute": can_execute,
            },
        )
        return response


@router.post("/devices/{device_id}/routerboot/stage", name="stage_routerboot_upgrade")
def stage_routerboot(request: Request, device_id: uuid.UUID, csrf: str = Form(...), confirmation: str = Form(...)):
    core.validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "firmware.execute")
        device = _load_device(db, device_id)
        _validate_modern_executor(db, device)
        current, target = _target_from_readiness(device)
        if current == target:
            raise HTTPException(409, "RouterBOOT risulta già aggiornato.")
        expected = f"ROUTERBOOT {target}"
        if confirmation.strip() != expected:
            raise HTTPException(400, f"Conferma non valida. Inserisci esattamente: {expected}")
        if _pending_job(db, device.id, {STAGE_JOB, REBOOT_JOB}):
            raise HTTPException(409, "Operazione RouterBOOT già in corso.")
        job = DeviceJob(
            device_id=device.id,
            job_type=STAGE_JOB,
            payload={"target_version": target, "observed_current": current, "reboot": False},
            expires_at=utcnow() + timedelta(minutes=10),
        )
        db.add(job)
        db.flush()
        _write_state(
            device,
            status="staging",
            target_version=target,
            current_before=current,
            stage_job_id=str(job.id),
            queued_at=utcnow().isoformat(),
            last_error=None,
        )
        core.add_event(
            db,
            "ROUTERBOOT_STAGE_QUEUED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={"job_id": str(job.id), "current": current, "target": target, "reboot": False},
            severity="warning",
            source="portal",
        )
        db.commit()
    return RedirectResponse(f"/devices/{device_id}/routerboot?stage=queued", status_code=303)


@router.post("/api/v1/agents/mikrotik/routerboot-stage/{job_id}/complete", name="mikrotik_routerboot_stage_complete")
async def stage_complete(request: Request, job_id: uuid.UUID):
    raw = await request.body()
    if len(raw) > MAX_RESULT_BODY:
        raise HTTPException(413, "Risultato RouterBOOT troppo grande.")
    payload = await agent_module._json_body(request)
    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type != STAGE_JOB:
            raise HTTPException(404, "Job RouterBOOT non trovato.")
        status = str(payload.get("status") or "failed").strip().lower()
        if status not in {"success", "failed"}:
            raise HTTPException(400, "Stato RouterBOOT non valido.")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        error = str(payload.get("error") or "").strip()[:4000] or None
        job.status = status
        job.result = result
        job.last_error = error
        job.completed_at = utcnow()
        target = str((job.payload or {}).get("target_version") or "").strip()
        if status == "success":
            _write_state(device, status="staged", target_version=target, staged_at=utcnow().isoformat(), last_error=None)
        else:
            _write_state(device, status="failed", target_version=target, failed_at=utcnow().isoformat(), last_error=error or "RouterBOOT stage failed")
        core.add_event(
            db,
            "ROUTERBOOT_STAGE_COMPLETED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={"job_id": str(job.id), "status": status, "target": target, "result": result},
            severity="warning" if status == "failed" else "info",
            result=status,
            source="mikrotik_agent",
        )
        db.commit()
    return {"status": "ok"}


@router.post("/devices/{device_id}/routerboot/reboot", name="reboot_for_routerboot_upgrade")
def reboot_routerboot(request: Request, device_id: uuid.UUID, csrf: str = Form(...), confirmation: str = Form(...)):
    core.validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "firmware.execute")
        device = _load_device(db, device_id)
        _validate_modern_executor(db, device)
        state = _state(device)
        if state.get("status") != "staged":
            raise HTTPException(409, "RouterBOOT deve essere staged prima del reboot.")
        target = str(state.get("target_version") or "").strip()
        if not target:
            raise HTTPException(409, "Target RouterBOOT mancante.")
        expected = f"REBOOT ROUTERBOOT {target}"
        if confirmation.strip() != expected:
            raise HTTPException(400, f"Conferma non valida. Inserisci esattamente: {expected}")
        if _pending_job(db, device.id, {STAGE_JOB, REBOOT_JOB}):
            raise HTTPException(409, "Operazione RouterBOOT già in corso.")
        job = DeviceJob(
            device_id=device.id,
            job_type=REBOOT_JOB,
            payload={"target_version": target, "action": "reboot_to_activate_routerboot"},
            expires_at=utcnow() + timedelta(minutes=10),
        )
        db.add(job)
        db.flush()
        _write_state(device, status="reboot_pending", reboot_job_id=str(job.id), reboot_queued_at=utcnow().isoformat(), last_error=None)
        core.add_event(
            db,
            "ROUTERBOOT_REBOOT_QUEUED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={"job_id": str(job.id), "target": target},
            severity="warning",
            source="portal",
        )
        db.commit()
    return RedirectResponse(f"/devices/{device_id}/routerboot?reboot=queued", status_code=303)


@router.post("/api/v1/agents/mikrotik/routerboot-reboot/{job_id}/ack", name="mikrotik_routerboot_reboot_ack")
async def reboot_ack(request: Request, job_id: uuid.UUID):
    raw = await request.body()
    if len(raw) > MAX_RESULT_BODY:
        raise HTTPException(413, "Risultato RouterBOOT troppo grande.")
    payload = await agent_module._json_body(request)
    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type != REBOOT_JOB:
            raise HTTPException(404, "Job reboot RouterBOOT non trovato.")
        status = str(payload.get("status") or "failed").strip().lower()
        if status not in {"accepted", "failed"}:
            raise HTTPException(400, "Stato reboot RouterBOOT non valido.")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        error = str(payload.get("error") or "").strip()[:4000] or None
        job.result = result
        job.last_error = error
        job.completed_at = utcnow()
        job.status = "success" if status == "accepted" else "failed"
        target = str((job.payload or {}).get("target_version") or "").strip()
        if status == "accepted":
            _write_state(device, status="rebooting", target_version=target, accepted_at=utcnow().isoformat(), last_error=None)
        else:
            _write_state(device, status="failed", target_version=target, failed_at=utcnow().isoformat(), last_error=error or "RouterBOOT reboot preflight failed")
        core.add_event(
            db,
            "ROUTERBOOT_REBOOT_ACK",
            customer_id=device.customer_id,
            device_id=device.id,
            details={"job_id": str(job.id), "status": status, "target": target, "result": result},
            severity="warning" if status == "failed" else "info",
            result="success" if status == "accepted" else "failed",
            source="mikrotik_agent",
        )
        db.commit()
    return {"status": "ok"}


def reconcile_from_readiness(db, device: Device, readiness: dict):
    """Close a RouterBOOT reboot only after a fresh read shows the target active."""
    state = _state(device)
    if state.get("status") not in {"rebooting", "reboot_pending"}:
        return None
    target = str(state.get("target_version") or "").strip()
    current = str(readiness.get("routerboard_current") or "").strip()
    if not target or not current:
        return None
    now = utcnow()
    accepted_at = _as_utc(state.get("accepted_at") or state.get("reboot_queued_at"))
    if current == target:
        _write_state(device, status="success", observed_version=current, verified_at=now.isoformat(), last_error=None)
        device.routerboot_version = current
        core.add_event(
            db,
            "ROUTERBOOT_UPGRADE_VERIFIED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={"target": target, "observed": current},
            source="worker",
        )
        return "success"
    if accepted_at and now - accepted_at > VERIFY_TIMEOUT:
        error = f"RouterBOOT non verificato entro {int(VERIFY_TIMEOUT.total_seconds() // 60)} minuti: atteso {target}, osservato {current}."
        _write_state(device, status="failed", observed_version=current, failed_at=now.isoformat(), last_error=error)
        core.add_event(
            db,
            "ROUTERBOOT_UPGRADE_VERIFY_FAILED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={"target": target, "observed": current},
            severity="error",
            result="failed",
            source="worker",
        )
        return "failed"
    return None


def _phase_job_lost(db, job_id) -> bool:
    """True when the phase job is missing or ended without its own report.

    Agent-reported failures already move the workflow state in the completion
    handlers; a failed job still referenced by an active state was therefore
    expired by job maintenance (never delivered or never answered).
    """
    try:
        job = db.get(DeviceJob, uuid.UUID(str(job_id)))
    except (TypeError, ValueError):
        return True
    return job is None or job.status == "failed"


def _recover_lost_phase(db, device: Device, state: dict, now) -> str | None:
    status = state.get("status")
    if status == "staging" and _phase_job_lost(db, state.get("stage_job_id")):
        _write_state(device, status="failed", failed_at=now.isoformat(), last_error=STAGE_LOST_ERROR)
        event, result = "ROUTERBOOT_STAGE_EXPIRED", "failed"
    elif status == "reboot_pending" and _phase_job_lost(db, state.get("reboot_job_id")):
        # The bootloader is already flashed; only the reboot never ran.
        _write_state(device, status="staged", reboot_job_id=None, last_error=REBOOT_LOST_ERROR)
        event, result = "ROUTERBOOT_REBOOT_EXPIRED", "reverted"
    else:
        return None
    core.add_event(
        db,
        event,
        customer_id=device.customer_id,
        device_id=device.id,
        details={"target": state.get("target_version"), "previous_status": status},
        severity="warning",
        result="failed",
        source="worker",
    )
    return result


def verification_tick():
    """After reboot, queue a read-only readiness refresh and expire stale verifies."""
    stats = {"queued": 0, "failed": 0, "reverted": 0}
    now = utcnow()
    with SessionLocal() as db:
        devices = db.scalars(select(Device).where(Device.vendor == "mikrotik")).all()
        for device in devices:
            state = _state(device)
            recovered = _recover_lost_phase(db, device, state, now)
            if recovered:
                stats[recovered] += 1
                continue
            if state.get("status") not in {"rebooting", "reboot_pending"}:
                continue
            accepted_at = _as_utc(state.get("accepted_at") or state.get("reboot_queued_at"))
            if accepted_at and now - accepted_at > VERIFY_TIMEOUT:
                readiness = _readiness(device)
                result = reconcile_from_readiness(db, device, readiness)
                if result is None and state.get("status") == "rebooting":
                    # No post-reboot RouterBOOT reading arrived at all.
                    _write_state(device, status="failed", failed_at=now.isoformat(), last_error=VERIFY_NO_DATA_ERROR)
                    core.add_event(
                        db,
                        "ROUTERBOOT_UPGRADE_VERIFY_FAILED",
                        customer_id=device.customer_id,
                        device_id=device.id,
                        details={"target": state.get("target_version"), "observed": None},
                        severity="warning",
                        result="failed",
                        source="worker",
                    )
                    result = "failed"
                if result == "failed":
                    stats["failed"] += 1
                continue
            if device.status != "online" or not accepted_at or now - accepted_at < VERIFY_GRACE:
                continue
            pending = _pending_job(db, device.id, {"firmware_readiness"})
            if pending:
                continue
            job = DeviceJob(
                device_id=device.id,
                job_type="firmware_readiness",
                payload={"read_only": True, "reason": "routerboot_post_reboot_verify"},
                expires_at=now + timedelta(minutes=10),
            )
            db.add(job)
            stats["queued"] += 1
        db.commit()
    return stats


def install_routerboot_lifecycle(app):
    _install_agent_handlers()
    app.include_router(router)
