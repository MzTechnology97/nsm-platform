"""Controlled MikroTik agent source update and rollback.

The server never accepts or forwards arbitrary RouterOS source.  Update source is
always generated locally from the currently installed NSM agent generator and
is exposed only to the authenticated device/job that requested it.

RouterOS 7.12 legacy stays reinstall-only because its deliberately restricted
read/test privilege profile must not be widened just to support self-update.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import or_, select

from app import main as core
from app import mikrotik_agent as agent
from app import mikrotik_backup_agent as backup_agent
from app import mikrotik_legacy as legacy
from app import mikrotik_operational_tools as operational_tools
from app import mikrotik_snapshot_agent as snapshot_agent
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.models import Device, utcnow
from app.security import validate_csrf

router = APIRouter()
TARGET_AGENT_VERSION = "0.49.0"
UPDATE_PROTOCOL = "source-v1"
UPDATE_JOB_TYPE = "agent_self_update"
UPDATE_JOB_TTL = timedelta(minutes=30)
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _sha512_text(value: str) -> str:
    return hashlib.sha512(value.encode("utf-8")).hexdigest()


def _version_tuple(value: str | None) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(str(value or ""))
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _base_version(value: str | None) -> str | None:
    parsed = _version_tuple(value)
    return ".".join(str(part) for part in parsed) if parsed else None


def _target_version_for_transport(transport: str) -> str:
    return f"{TARGET_AGENT_VERSION}-legacy" if transport == "legacy" else TARGET_AGENT_VERSION


def agent_update_status(device: Device) -> dict:
    data = dict(device.inventory_data or {})
    transport = str(data.get("agent_transport") or "unknown").strip().lower()
    current = str(data.get("agent_version") or "").strip() or None
    current_base = _base_version(current)
    target = _target_version_for_transport(transport)
    target_base = TARGET_AGENT_VERSION
    expected_hash = str(data.get("agent_expected_source_sha512") or "").strip() or None
    observed_hash = str(data.get("agent_source_sha512") or "").strip() or None
    drift = bool(expected_hash and observed_hash and expected_hash != observed_hash)
    version_outdated = bool(current_base and current_base != target_base)
    missing_version = not current_base
    self_update_capable = bool(
        transport == "modern"
        and current_base
        and _version_tuple(current_base) >= _version_tuple(TARGET_AGENT_VERSION)
    )
    migration_required = bool(data.get("compatibility_migration_required"))
    outdated = version_outdated or missing_version or drift or migration_required
    return {
        "target_version": target,
        "current_version": current,
        "outdated": outdated,
        "source_drift": drift,
        "self_update_capable": self_update_capable,
        "requires_reinstall": bool(outdated and not self_update_capable),
        "state": data.get("agent_update_state") or "idle",
        "expected_source_sha512": expected_hash,
        "observed_source_sha512": observed_hash,
        "requested_at": data.get("agent_update_requested_at"),
        "delivered_at": data.get("agent_update_delivered_at"),
        "verified_at": data.get("agent_update_verified_at"),
        "protocol": UPDATE_PROTOCOL if self_update_capable else "reinstall-only",
    }


def _inject_integrity_probe(source: str) -> str:
    inventory_marker = ":local nsmInventory {"
    if inventory_marker not in source:
        raise RuntimeError("MikroTik agent inventory marker not found")
    probe = (
        ':local nsmAgentSourceSha512 ""\n'
        ':do { :set nsmAgentSourceSha512 [:convert [/system script get [find where name="nsm-agent-heartbeat"] source] transform=sha512] } on-error={}\n'
    )
    source = source.replace(inventory_marker, probe + inventory_marker, 1)
    version_marker = f'"agent_version"="{TARGET_AGENT_VERSION}"'
    if version_marker not in source:
        raise RuntimeError("MikroTik agent version marker not found")
    return source.replace(
        version_marker,
        version_marker + ';"agent_source_sha512"=$nsmAgentSourceSha512',
        1,
    )


def _self_update_handler(base_url: str, check_certificate: bool) -> str:
    cert = " check-certificate=yes" if check_certificate else ""
    source_base = f"{base_url}/api/v1/agents/mikrotik/self-update/"
    return f'''
    :if ($nsmJobType = "{UPDATE_JOB_TYPE}") do={{
      :local nsmUpdateOk true
      :local nsmRolledBack false
      :local nsmUpdateError ""
      :local nsmScriptId [/system script find where name="nsm-agent-heartbeat"]
      :local nsmOldSource ""
      :do {{
        :if ([:len $nsmScriptId] = 0) do={{ :error "NSM agent script not found" }}
        :set nsmOldSource [/system script get $nsmScriptId source]
        :local nsmSourceUrl ("{source_base}" . $nsmJobId . "/source")
        :local nsmSourceResult [/tool fetch url=$nsmSourceUrl http-header-field=$nsmHeaders output=user as-value{cert}]
        :if (($nsmSourceResult->"status") != "finished") do={{ :error "NSM agent source download failed" }}
        :local nsmNewSource ($nsmSourceResult->"data")
        :if ([:len $nsmNewSource] < 100) do={{ :error "NSM agent source invalid" }}
        :do {{ /system script remove [find where name="nsm-agent-heartbeat-prev"] }} on-error={{}}
        :local nsmOldPolicy [/system script get $nsmScriptId policy]
        /system script add name="nsm-agent-heartbeat-prev" policy=$nsmOldPolicy source=$nsmOldSource comment="NSM previous-known-good agent"
        /system script set $nsmScriptId source=$nsmNewSource
        /system script run $nsmScriptId
      }} on-error={{
        :set nsmUpdateOk false
        :set nsmRolledBack true
        :set nsmUpdateError "Agent update failed; previous-known-good restored"
        :if ([:len $nsmOldSource] > 0) do={{ :do {{ /system script set $nsmScriptId source=$nsmOldSource }} on-error={{}} }}
      }}
      :local nsmUpdateStatus "failed"
      :if ($nsmUpdateOk) do={{ :set nsmUpdateStatus "success" }}
      :local nsmCompleteUrl ("{source_base}" . $nsmJobId . "/complete?status=" . $nsmUpdateStatus . "&rolled_back=" . $nsmRolledBack)
      :do {{ /tool fetch url=$nsmCompleteUrl http-method=post http-header-field=$nsmHeaders http-data="" output=user as-value{cert} }} on-error={{ :log warning "NSM agent update completion failed" }}
    }}
'''


def _extend_modern_source(previous):
    def wrapped(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        source = _inject_integrity_probe(source)
        marker = '    :if ($nsmJobType = "backup_mikrotik") do={'
        if marker not in source:
            raise RuntimeError("MikroTik self-update extension point not found")
        return source.replace(
            marker,
            _self_update_handler(base_url, check_certificate) + "\n" + marker,
            1,
        )

    return wrapped


def _persist_expected_source(device_id: uuid.UUID, source: str, transport: str):
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        if not device:
            return
        data = dict(device.inventory_data or {})
        data["agent_expected_version"] = _target_version_for_transport(transport)
        data["agent_expected_source_sha512"] = _sha512_text(source)
        data["agent_update_protocol"] = UPDATE_PROTOCOL if transport == "modern" else "reinstall-only"
        data["agent_source_drift"] = False
        device.inventory_data = data
        db.commit()


def _wrap_bodyless_enrollment(previous):
    def wrapped(request, raw_token, observed_version):
        device_id, source, transport = previous(request, raw_token, observed_version)
        _persist_expected_source(device_id, source, transport)
        return device_id, source, transport

    return wrapped


def _wrap_inventory_updates(previous):
    def wrapped(db, device, inventory, request, source):
        result = previous(db, device, inventory, request, source)
        data = dict(device.inventory_data or {})
        observed_hash = str(inventory.get("agent_source_sha512") or "").strip() or None
        if observed_hash:
            data["agent_source_sha512"] = observed_hash
        expected_hash = str(data.get("agent_expected_source_sha512") or "").strip() or None
        reported_version = str(inventory.get("agent_version") or data.get("agent_version") or "").strip()
        expected_version = str(data.get("agent_expected_version") or "").strip()
        drift = bool(expected_hash and observed_hash and expected_hash != observed_hash)
        data["agent_source_drift"] = drift
        if (
            data.get("agent_update_state") in {"installing", "awaiting_heartbeat", "expired"}
            and expected_version
            and reported_version == expected_version
            and not drift
            and observed_hash
        ):
            data["agent_update_state"] = "verified"
            data["agent_update_verified_at"] = utcnow().isoformat()
            core.add_event(
                db,
                "MIKROTIK_AGENT_UPDATE_VERIFIED",
                customer_id=device.customer_id,
                device_id=device.id,
                source="mikrotik_agent",
                details={"agent_version": reported_version, "source_sha512": observed_hash},
            )
        device.inventory_data = data
        return result

    return wrapped


@router.post("/devices/{device_id}/agent/update", name="queue_mikrotik_agent_update")
def queue_agent_update(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.enroll")
        device = db.get(Device, device_id)
        if not device or device.vendor != "mikrotik":
            raise HTTPException(404)
        credential = db.scalar(
            select(DeviceAgentCredential).where(
                DeviceAgentCredential.device_id == device.id,
                DeviceAgentCredential.agent_type == "mikrotik_agent",
                DeviceAgentCredential.is_active.is_(True),
            )
        )
        if not credential:
            raise HTTPException(409, "Agent non associato.")
        status = agent_update_status(device)
        if str((device.inventory_data or {}).get("agent_transport") or "") != "modern":
            raise HTTPException(409, "Il self-update richiede il transport moderno; usare la reinstallazione guidata.")
        if not status["self_update_capable"]:
            raise HTTPException(409, "Questo agent precede il protocollo self-update; eseguire una reinstallazione guidata una sola volta.")
        if not status["outdated"]:
            raise HTTPException(409, "Agent gia aggiornato e senza drift rilevato.")
        active = db.scalar(
            select(DeviceJob.id).where(
                DeviceJob.device_id == device.id,
                DeviceJob.job_type == UPDATE_JOB_TYPE,
                DeviceJob.status.in_(["pending", "delivered", "running"]),
            )
        )
        if active:
            raise HTTPException(409, "Aggiornamento agent gia in corso.")
        data = dict(device.inventory_data or {})
        job = DeviceJob(
            device_id=device.id,
            job_type=UPDATE_JOB_TYPE,
            status="pending",
            payload={
                "target_version": TARGET_AGENT_VERSION,
                "previous_agent_version": data.get("agent_version"),
                "previous_expected_version": data.get("agent_expected_version"),
                "previous_expected_sha512": data.get("agent_expected_source_sha512"),
            },
            expires_at=utcnow() + UPDATE_JOB_TTL,
        )
        db.add(job)
        db.flush()
        data["agent_update_state"] = "pending"
        data["agent_update_job_id"] = str(job.id)
        data["agent_update_requested_at"] = utcnow().isoformat()
        device.inventory_data = data
        core.add_event(
            db,
            "MIKROTIK_AGENT_UPDATE_QUEUED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            source="portal",
            details={"job_id": str(job.id), "target_version": TARGET_AGENT_VERSION},
        )
        db.commit()
    return RedirectResponse(f"/devices/{device_id}/agent", status_code=303)


@router.get(
    "/api/v1/agents/mikrotik/self-update/{job_id}/source",
    response_class=PlainTextResponse,
    name="mikrotik_agent_update_source",
)
def agent_update_source(request: Request, job_id: uuid.UUID):
    with SessionLocal() as db:
        device, _ = agent._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type != UPDATE_JOB_TYPE:
            raise HTTPException(404, "Job self-update non trovato.")
        if job.status not in {"delivered", "running"}:
            raise HTTPException(409, "Job self-update non consegnato.")
        data = dict(device.inventory_data or {})
        if str(data.get("agent_transport") or "") != "modern":
            raise HTTPException(409, "Self-update non supportato dal transport corrente.")
        raw_secret = request.headers.get("X-NSM-Device-Secret", "").strip()
        base_url = str(request.base_url).rstrip("/")
        source = agent._agent_source(base_url, device.id, raw_secret, base_url.lower().startswith("https://"))
        digest = _sha512_text(source)
        data["agent_expected_version"] = TARGET_AGENT_VERSION
        data["agent_expected_source_sha512"] = digest
        data["agent_update_state"] = "installing"
        data["agent_update_delivered_at"] = utcnow().isoformat()
        data["agent_update_protocol"] = UPDATE_PROTOCOL
        device.inventory_data = data
        job.status = "running"
        result = dict(job.result or {})
        result.update({"target_version": TARGET_AGENT_VERSION, "source_sha512": digest})
        job.result = result
        db.commit()
        return PlainTextResponse(
            source,
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-NSM-Source-SHA512": digest},
        )


@router.post(
    "/api/v1/agents/mikrotik/self-update/{job_id}/complete",
    name="mikrotik_agent_update_complete",
)
def agent_update_complete(
    request: Request,
    job_id: uuid.UUID,
    status: str = Query("failed"),
    rolled_back: bool = Query(False),
):
    normalized = status.strip().lower()
    if normalized not in {"success", "failed"}:
        raise HTTPException(400, "Stato self-update non valido.")
    with SessionLocal() as db:
        device, _ = agent._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type != UPDATE_JOB_TYPE:
            raise HTTPException(404, "Job self-update non trovato.")
        data = dict(device.inventory_data or {})
        if rolled_back or normalized == "failed":
            data["agent_update_state"] = "rolled_back" if rolled_back else "failed"
            if job.payload.get("previous_expected_version"):
                data["agent_expected_version"] = job.payload["previous_expected_version"]
            if job.payload.get("previous_expected_sha512"):
                data["agent_expected_source_sha512"] = job.payload["previous_expected_sha512"]
            data["agent_source_drift"] = False if rolled_back else data.get("agent_source_drift", False)
            job.status = "failed"
            job.last_error = "Agent update rolled back" if rolled_back else "Agent update failed"
        else:
            if data.get("agent_update_state") != "verified":
                data["agent_update_state"] = "awaiting_heartbeat"
            job.status = "success"
            job.last_error = None
        job.completed_at = utcnow()
        result = dict(job.result or {})
        result.update({"rolled_back": bool(rolled_back), "reported_status": normalized})
        job.result = result
        device.inventory_data = data
        core.add_event(
            db,
            "MIKROTIK_AGENT_UPDATE_COMPLETED",
            customer_id=device.customer_id,
            device_id=device.id,
            severity="warning" if job.status == "failed" else "info",
            result=job.status,
            source="mikrotik_agent",
            details={"job_id": str(job.id), "rolled_back": bool(rolled_back), "state": data["agent_update_state"]},
        )
        db.commit()
    return {"status": "ok", "state": data["agent_update_state"]}


RUNNING_EXPIRED_ERROR = "Self-update avviato ma senza esito entro la finestra prevista."


def reconcile_agent_update_states(now=None) -> int:
    """Own the timeout of running self-updates and close expired update states.

    Generic job maintenance expires pending/delivered jobs but leaves *running*
    jobs to their domain. A self-update becomes running once the router fetched
    its source; if the router never reports back (reboot, lost link) the job
    would stay running forever and block every later update. Expired jobs move
    the Device state to "expired"; a later heartbeat that proves the target
    version and source hash still moves it to "verified".
    """
    now = now or utcnow()
    closed = 0
    with SessionLocal() as db:
        for job in db.scalars(
            select(DeviceJob).where(
                DeviceJob.job_type == UPDATE_JOB_TYPE,
                DeviceJob.status == "running",
                DeviceJob.expires_at.is_not(None),
                DeviceJob.expires_at <= now,
            )
        ):
            job.status = "failed"
            job.last_error = RUNNING_EXPIRED_ERROR
            job.completed_at = now
        db.flush()
        jobs = list(
            db.scalars(
                select(DeviceJob).where(
                    DeviceJob.job_type == UPDATE_JOB_TYPE,
                    DeviceJob.status == "failed",
                    DeviceJob.completed_at.is_not(None),
                    DeviceJob.completed_at >= now - timedelta(days=1),
                )
            )
        )
        for job in jobs:
            device = db.get(Device, job.device_id)
            if not device:
                continue
            data = dict(device.inventory_data or {})
            if data.get("agent_update_job_id") != str(job.id):
                continue
            if data.get("agent_update_state") not in {"pending", "installing"}:
                continue
            data["agent_update_state"] = "expired"
            device.inventory_data = data
            core.add_event(
                db,
                "MIKROTIK_AGENT_UPDATE_EXPIRED",
                customer_id=device.customer_id,
                device_id=device.id,
                severity="warning",
                result="failed",
                source="worker",
                details={"job_id": str(job.id), "error": job.last_error},
            )
            closed += 1
        db.commit()
    return closed


def install_mikrotik_agent_self_update(app):
    previous_source = agent._agent_source
    agent.AGENT_VERSION = TARGET_AGENT_VERSION
    backup_agent.AGENT_VERSION = TARGET_AGENT_VERSION
    snapshot_agent.AGENT_VERSION = TARGET_AGENT_VERSION
    operational_tools.AGENT_VERSION = TARGET_AGENT_VERSION
    agent._agent_source = _extend_modern_source(previous_source)
    legacy._issue_bodyless_credential = _wrap_bodyless_enrollment(legacy._issue_bodyless_credential)
    agent._apply_inventory = _wrap_inventory_updates(agent._apply_inventory)
    app.include_router(router)
