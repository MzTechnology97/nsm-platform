"""Reboot and RouterOS upgrade for legacy Agents (RouterOS 6.48/6.49 and 7.12).

Legacy Agents installed with the ``legacy-ops-v1`` profile can run two fixed
handlers compiled into the Agent:

* ``device_reboot`` — the same controlled reboot as modern Agents;
* ``legacy_firmware_upgrade`` — re-checks the update channel, refuses when the
  latest version differs from the operator-approved target, then runs
  ``/system package update install`` (download + reboot).

Both acknowledge the delivered job to NSM (bodyless POST answered ``ok``)
before acting: no acknowledgement, no reboot.  NSM verifies the reboot by
uptime and the upgrade by the version reported after the reboot.  Upgrading a
7.12 router to 7.13+ is the path to the modern Agent (reinstall afterwards).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent_module
from app import mikrotik_legacy as legacy
from app import mikrotik_legacy_jobs as legacy_jobs
from app import mikrotik_device_reboot as reboot
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.models import Device, utcnow
from app.routeros_version import is_newer_routeros_version, parse_routeros_version
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
UPGRADE_JOB = "legacy_firmware_upgrade"
STATE_KEY = "legacy_upgrade"
LEGACY_OPS_PROFILE = "legacy-ops-v1"
READINESS_MAX_AGE = timedelta(hours=24)
UPGRADE_TIMEOUT = timedelta(minutes=30)
_TARGET_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?(?:(?:beta|rc)\d+)?$")
STATUS_LABELS = {
    "queued": "In coda: l'agent lo riceverà al prossimo heartbeat",
    "upgrading": "Aggiornamento confermato dall'agent: download e riavvio in corso",
    "verified": "Aggiornamento verificato",
    "failed": "Aggiornamento non riuscito",
}


def _handlers(base_url: str, check_certificate: bool) -> str:
    cert = " check-certificate=yes" if check_certificate else ""
    ack = f'("{base_url}/api/v1/agents/mikrotik/legacy/jobs/" . $nsmJobId . "/ack")'
    return f'''            :if ($nsmJobType = "device_reboot") do={{
              :local nsmAck [/tool fetch url={ack} http-method=post http-header-field=$nsmLegacyHeaders http-data="" output=user as-value{cert}]
              :if ((($nsmAck->"status") = "finished") && (($nsmAck->"data") = "ok")) do={{
                :log warning "NSM requested reboot: restarting in 3 seconds"
                :delay 3s
                /system reboot
              }} else={{ :error "NSM reboot not acknowledged" }}
            }}
            :if ($nsmJobType = "{UPGRADE_JOB}") do={{
              /system package update check-for-updates once
              :local nsmWait 0
              :while (($nsmWait < 30) && ([:len [/system package update get latest-version]] = 0)) do={{ :delay 1s; :set nsmWait ($nsmWait + 1) }}
              :local nsmLatest [/system package update get latest-version]
              :if ($nsmLatest != $nsmArg1) do={{ :error ("NSM upgrade target changed: latest " . $nsmLatest) }}
              :local nsmAck [/tool fetch url={ack} http-method=post http-header-field=$nsmLegacyHeaders http-data="" output=user as-value{cert}]
              :if ((($nsmAck->"status") = "finished") && (($nsmAck->"data") = "ok")) do={{
                :log warning ("NSM requested RouterOS upgrade to " . $nsmArg1)
                /system package update install
              }} else={{ :error "NSM upgrade not acknowledged" }}
            }}
'''


def _as_utc(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _state(device) -> dict:
    return dict((device.inventory_data or {}).get(STATE_KEY) or {})


def _write_state(device, **changes) -> dict:
    data = dict(device.inventory_data or {})
    state = dict(data.get(STATE_KEY) or {})
    state.update(changes)
    data[STATE_KEY] = state
    device.inventory_data = data
    return state


def _base(version) -> str:
    return str(version or "").split(" ")[0].strip()


def _ops_ready(db, device) -> str | None:
    data = device.inventory_data or {}
    if str(data.get("agent_transport") or "") != "legacy":
        return "Operazione riservata agli agent legacy."
    if str(data.get("agent_privilege_profile") or "") != LEGACY_OPS_PROFILE:
        return "L'agent legacy installato è in sola lettura: reinstallalo con un nuovo token (profilo legacy-ops-v1, agent 0.49.6+) per abilitare riavvio e aggiornamento."
    active = db.scalar(
        select(DeviceAgentCredential.id).where(
            DeviceAgentCredential.device_id == device.id,
            DeviceAgentCredential.agent_type == "mikrotik_agent",
            DeviceAgentCredential.is_active.is_(True),
        )
    )
    if not active or device.status != "online":
        return "L'apparato deve essere online con credenziale agent attiva."
    busy = db.scalar(
        select(DeviceJob.job_type).where(
            DeviceJob.device_id == device.id,
            DeviceJob.job_type.in_(sorted(reboot.CONFLICTING_JOBS | {UPGRADE_JOB})),
            DeviceJob.status.in_(["pending", "delivered", "running"]),
        )
    )
    if busy:
        return f"Operazione in corso sull'apparato ({busy}): attendi che termini."
    return None


def upgrade_target(device, now) -> tuple[str | None, str | None]:
    """(target, blocker) from the latest firmware readiness of a legacy Device."""
    readiness = dict((device.inventory_data or {}).get("firmware_readiness") or {})
    latest = _base(readiness.get("latest_version"))
    installed = _base(device.firmware_version)
    checked = _as_utc(readiness.get("checked_at"))
    if not latest or not checked:
        return None, "Esegui prima una verifica firmware (readiness) dalla worklist Firmware."
    if now - checked > READINESS_MAX_AGE:
        return None, "La verifica firmware ha più di 24 ore: ripetila prima di aggiornare."
    if not _TARGET_RE.match(latest):
        return None, f"Versione proposta non riconosciuta: {latest}."
    newer = is_newer_routeros_version(latest, installed)
    if newer is None:
        return None, "Versione installata o proposta non confrontabile."
    if not newer:
        return None, f"RouterOS {installed} è già aggiornato sul canale {readiness.get('channel') or '—'}."
    if parse_routeros_version(latest)[0] != parse_routeros_version(installed)[0]:
        return None, "Il passaggio tra versioni major (es. 6 → 7) non è automatizzato: eseguilo manualmente."
    return latest, None


def legacy_upgrade_info(device) -> dict | None:
    """Template helper for the Firmware tab of legacy Devices."""
    if str((device.inventory_data or {}).get("agent_transport") or "") != "legacy":
        return None
    with SessionLocal() as db:
        fresh = db.get(Device, device.id)
        target, target_blocker = upgrade_target(fresh, utcnow())
        return {
            "target": target,
            "blocker": _ops_ready(db, fresh) or target_blocker,
            "state": _state(fresh),
            "labels": STATUS_LABELS,
            "readiness": dict((fresh.inventory_data or {}).get("firmware_readiness") or {}),
        }


def legacy_reboot_blocker(previous):
    def wrapped(db, device):
        if str((device.inventory_data or {}).get("agent_transport") or "") == "legacy":
            return _ops_ready(db, device)
        return previous(db, device)

    return wrapped


@router.post("/devices/{device_id}/legacy-upgrade", name="queue_legacy_firmware_upgrade")
async def queue_upgrade(request: Request, device_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    back = f"/devices/{device_id}/firmware-upgrade"
    with SessionLocal() as db:
        user = core.require_permission(request, db, "firmware.execute")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        now = utcnow()
        blocker = _ops_ready(db, device)
        target, target_blocker = upgrade_target(device, now)
        if blocker or target_blocker:
            return flash_redirect(request, back, "warning", blocker or target_blocker, title="Aggiornamento non disponibile")
        if str(form.get("confirmation") or "").strip() != f"AGGIORNA {target}":
            return flash_redirect(request, back, "warning", f"Per confermare scrivi esattamente AGGIORNA {target}.", title="Conferma mancante")
        if form.get("backup_ack") is None:
            return flash_redirect(request, back, "warning", "Conferma di avere un export o backup recente: gli agent legacy non eseguono backup NSM.", title="Backup non confermato")
        job = DeviceJob(device_id=device.id, job_type=UPGRADE_JOB, payload={"target_version": target}, expires_at=now + timedelta(minutes=15))
        db.add(job)
        db.flush()
        _write_state(device, status="queued", job_id=str(job.id), target_version=target, from_version=_base(device.firmware_version), queued_at=now.isoformat(), requested_by=user.username, accepted_at=None, verified_at=None, last_error=None)
        core.add_event(db, "LEGACY_FIRMWARE_UPGRADE_QUEUED", actor=user, customer_id=device.customer_id, device_id=device.id, details={"job_id": str(job.id), "from": _base(device.firmware_version), "target": target}, severity="warning", source="portal")
        db.commit()
    return flash_redirect(request, back, "success", f"Aggiornamento a RouterOS {target} in coda: l'agent lo eseguirà al prossimo heartbeat; il router si riavvierà.", title="Aggiornamento richiesto")


@router.post("/api/v1/agents/mikrotik/legacy/jobs/{job_id}/ack", response_class=PlainTextResponse, name="mikrotik_legacy_job_ack")
def legacy_ack(request: Request, job_id: uuid.UUID):
    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type not in {reboot.REBOOT_JOB, UPGRADE_JOB}:
            raise HTTPException(404, "Job non trovato.")
        if job.status != "delivered":
            raise HTTPException(409, "Job non più valido.")
        now = utcnow()
        job.status = "running"
        uptime = (device.inventory_data or {}).get("uptime")
        if job.job_type == reboot.REBOOT_JOB:
            job.result = {"uptime_before": str(uptime or "")[:80]}
            reboot._write_state(device, status="rebooting", accepted_at=now.isoformat(), uptime_before=job.result["uptime_before"])
            event = "DEVICE_REBOOT_ACCEPTED"
        else:
            _write_state(device, status="upgrading", accepted_at=now.isoformat())
            event = "LEGACY_FIRMWARE_UPGRADE_ACCEPTED"
        core.add_event(db, event, customer_id=device.customer_id, device_id=device.id, details={"job_id": str(job.id), "transport": "routeros_legacy"}, severity="warning", source="mikrotik_agent_legacy")
        db.commit()
    return PlainTextResponse("ok", headers={"Cache-Control": "no-store"})


def verify_upgrades(now=None) -> dict:
    now = now or utcnow()
    stats = {"verified": 0, "failed": 0}
    with SessionLocal() as db:
        for device in db.scalars(select(Device).where(Device.vendor == "mikrotik")):
            state = _state(device)
            if state.get("status") not in {"queued", "upgrading"}:
                continue
            job = db.get(DeviceJob, uuid.UUID(state["job_id"])) if state.get("job_id") else None
            target = state.get("target_version")
            if state["status"] == "queued":
                if job is None or job.status == "failed":
                    error = (job.last_error if job and job.last_error else None) or "L'agent non ha preso in carico l'aggiornamento: nessuna modifica eseguita."
                    _write_state(device, status="failed", last_error=error, failed_at=now.isoformat())
                    stats["failed"] += 1
                continue
            accepted = _as_utc(state.get("accepted_at"))
            seen = _as_utc(device.last_seen)
            if seen and accepted and seen > accepted and _base(device.firmware_version) == target:
                _write_state(device, status="verified", verified_at=now.isoformat(), observed_version=_base(device.firmware_version), last_error=None)
                if job:
                    job.status = "success"
                    job.completed_at = now
                    job.result = {**(job.result or {}), "observed_version": _base(device.firmware_version)}
                stats["verified"] += 1
                core.add_event(db, "LEGACY_FIRMWARE_UPGRADE_VERIFIED", customer_id=device.customer_id, device_id=device.id, details={"target": target, "observed": _base(device.firmware_version)}, source="worker")
            elif accepted and now - accepted > UPGRADE_TIMEOUT:
                error = f"RouterOS {target} non osservato entro {int(UPGRADE_TIMEOUT.total_seconds() // 60)} minuti (versione attuale {_base(device.firmware_version) or '—'})."
                _write_state(device, status="failed", last_error=error, failed_at=now.isoformat())
                if job:
                    job.status = "failed"
                    job.last_error = error
                    job.completed_at = now
                stats["failed"] += 1
                core.add_event(db, "LEGACY_FIRMWARE_UPGRADE_FAILED", customer_id=device.customer_id, device_id=device.id, details={"target": target, "observed": _base(device.firmware_version)}, severity="error", result="failed", source="worker")
        db.commit()
    return stats


def _install_agent_handlers():
    previous = legacy._legacy_agent_source

    def source_with_operations(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        marker = '            :if ($nsmJobType = "snapshot_section") do={'
        if marker not in source:
            raise RuntimeError("MikroTik legacy operations extension point not found")
        return source.replace(marker, _handlers(base_url, check_certificate) + marker, 1)

    legacy._legacy_agent_source = source_with_operations


def _install_job_line():
    previous = legacy_jobs._job_line

    def job_line(job):
        if job.job_type == UPGRADE_JOB:
            target = str((job.payload or {}).get("target_version") or "")
            if not _TARGET_RE.match(target):
                raise HTTPException(409, "Target di aggiornamento non valido.")
            return f"{job.id}|{UPGRADE_JOB}|{target}|"
        if job.job_type == reboot.REBOOT_JOB:
            return f"{job.id}|{reboot.REBOOT_JOB}||"
        return previous(job)

    legacy_jobs._job_line = job_line


def _install_completion_policy():
    """A success report means only "the Agent ran the command": verification decides."""
    previous = legacy_jobs.legacy_job_complete

    async def complete(request: Request, job_id: uuid.UUID, status: str = "failed"):
        with SessionLocal() as db:
            job = db.get(DeviceJob, job_id)
            job_type = job.job_type if job else None
        if job_type not in {reboot.REBOOT_JOB, UPGRADE_JOB}:
            return await previous(request, job_id, status)
        raw = (await request.body()).decode("utf-8", errors="replace")[:4000]
        with SessionLocal() as db:
            device, _ = agent_module._authenticate_agent(db, request)
            job = db.get(DeviceJob, job_id)
            if not job or job.device_id != device.id:
                raise HTTPException(404, "Job legacy non trovato.")
            if str(status).strip().lower() == "failed":
                job.status = "failed"
                job.last_error = raw or "Operazione legacy fallita sull'apparato."
                job.completed_at = utcnow()
                if job.job_type == UPGRADE_JOB:
                    _write_state(device, status="failed", last_error=job.last_error, failed_at=utcnow().isoformat())
                else:
                    reboot._write_state(device, status="failed", last_error=job.last_error, failed_at=utcnow().isoformat())
            db.commit()
        return {"status": "ok"}

    # The completion guard route delegates to this module attribute.
    legacy_jobs.legacy_job_complete = complete


def install_mikrotik_legacy_operations(app) -> None:
    legacy_jobs.LEGACY_JOB_TYPES = set(legacy_jobs.LEGACY_JOB_TYPES) | {reboot.REBOOT_JOB, UPGRADE_JOB}
    _install_agent_handlers()
    _install_job_line()
    _install_completion_policy()
    reboot.reboot_blocker = legacy_reboot_blocker(reboot.reboot_blocker)
    app.include_router(router)
