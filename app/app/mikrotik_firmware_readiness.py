"""Read-only MikroTik firmware readiness for Core 0.30.

This feature only asks RouterOS to check the selected update channel and records
the observed state. It never downloads packages, installs updates, upgrades the
RouterBOARD bootloader, or reboots the device.
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent_module
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.models import Device, utcnow
from app.routeros_version import compare_routeros_versions, is_newer_routeros_version

router = APIRouter()
JOB_TYPE = "firmware_readiness"
MAX_RESULT_BODY = 64 * 1024
_KV_RE = re.compile(r"(?:^|;)([a-z_]+)=([^;]*)")


def _clean(value, limit=200):
    value = str(value or "").strip()
    return value[:limit] if value else ""


def parse_legacy_firmware_output(value: str) -> dict:
    return {key: val.strip() for key, val in _KV_RE.findall(str(value or ""))}


def apply_firmware_readiness(db, device: Device, result: dict, *, source: str):
    installed = _clean(result.get("installed_version") or result.get("installed"), 150)
    latest = _clean(result.get("latest_version") or result.get("latest"), 150)
    status = _clean(result.get("status"), 200)
    channel = _clean(result.get("channel"), 40)
    free_hdd = _clean(result.get("free_hdd_space") or result.get("free_hdd"), 80)
    rb_current = _clean(result.get("routerboard_current") or result.get("rb_current"), 100)
    rb_upgrade = _clean(result.get("routerboard_upgrade") or result.get("rb_upgrade"), 100)

    if installed:
        device.firmware_version = installed
    lowered = status.lower()
    relation = "unknown"
    newer = is_newer_routeros_version(latest, installed) if installed and latest else None
    if newer is True:
        relation = "newer"
        device.recommended_firmware_version = latest
        device.firmware_status = "update_available"
    elif newer is False:
        # The channel may publish an older build than the installed one (e.g.
        # long-term channel on a stable install). That is never an update.
        relation = "same" if compare_routeros_versions(latest, installed) == 0 else "older"
        device.recommended_firmware_version = None
        device.firmware_status = "current"
    else:
        if latest:
            device.recommended_firmware_version = latest
        if installed and latest:
            if installed == latest or "already up to date" in lowered:
                device.firmware_status = "current"
            else:
                device.firmware_status = "update_available"
        elif "new version" in lowered:
            device.firmware_status = "update_available"

    data = dict(device.inventory_data or {})
    data["firmware_readiness"] = {
        "channel": channel or None,
        "installed_version": installed or None,
        "latest_version": latest or None,
        "status": status or None,
        "free_hdd_space": free_hdd or None,
        "routerboard_current": rb_current or None,
        "routerboard_upgrade": rb_upgrade or None,
        "latest_relation": relation,
        "checked_at": utcnow().isoformat(),
        "source": source,
    }
    device.inventory_data = data
    if rb_current:
        device.routerboot_version = rb_current
    device.inventory_last_verified_at = utcnow()

    # Core 0.38: a fresh read-only readiness result is also the authoritative
    # post-reboot verifier for the separately gated RouterBOOT lifecycle.
    try:
        from app.routerboot_lifecycle import reconcile_from_readiness
        reconcile_from_readiness(db, device, data["firmware_readiness"])
    except ImportError:
        pass

    return data["firmware_readiness"]


_HANDLER = r'''
    :if ($nsmJobType = "firmware_readiness") do={
      :local nsmOk true
      :local nsmError ""
      :local nsmChannel ""
      :local nsmInstalled ""
      :local nsmLatest ""
      :local nsmUpdateStatus ""
      :local nsmFreeHdd ""
      :local nsmRbCurrent ""
      :local nsmRbUpgrade ""
      :do {
        :set nsmChannel [/system package update get channel]
        /system package update check-for-updates once
        :delay 2s
        :set nsmInstalled [/system package update get installed-version]
        :set nsmLatest [/system package update get latest-version]
        :set nsmUpdateStatus [/system package update get status]
        :set nsmFreeHdd [/system resource get free-hdd-space]
        :do { :set nsmRbCurrent [/system routerboard get current-firmware] } on-error={}
        :do { :set nsmRbUpgrade [/system routerboard get upgrade-firmware] } on-error={}
      } on-error={ :set nsmOk false; :set nsmError "RouterOS check-for-updates failed" }
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/firmware-readiness/" . $nsmJobId . "/complete")
      :local nsmStatus "failed"
      :if ($nsmOk) do={ :set nsmStatus "success" }
      :local nsmBody [:serialize value={"status"=$nsmStatus;"error"=$nsmError;"result"={"channel"=$nsmChannel;"installed_version"=$nsmInstalled;"latest_version"=$nsmLatest;"status"=$nsmUpdateStatus;"free_hdd_space"=[:tostr $nsmFreeHdd];"routerboard_current"=$nsmRbCurrent;"routerboard_upgrade"=$nsmRbUpgrade}} to=json options=json.no-string-conversion]
      :do { /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmBody output=user as-value } on-error={ :log warning "NSM firmware readiness upload failed" }
    }
'''


def _install_agent_handler():
    previous = agent_module._agent_source

    def source_with_firmware_readiness(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        marker = '    :if ($nsmJobType = "backup_mikrotik") do={'
        if marker not in source:
            raise RuntimeError("MikroTik firmware-readiness extension point not found")
        return source.replace(marker, _HANDLER + "\n" + marker, 1)

    agent_module._agent_source = source_with_firmware_readiness


def _queue_redirect(device_id, return_to: str, state: str):
    if return_to == "firmware":
        return RedirectResponse(f"/operations/firmware?firmware_check={state}", status_code=303)
    return RedirectResponse(f"/devices/{device_id}?firmware_check={state}", status_code=303)


def queue_readiness_job(db, device, user) -> str:
    """Queue a read-only readiness check: "queued", "already_queued" or "agent_required".

    The caller commits.
    """
    credential = db.scalar(
        select(DeviceAgentCredential).where(
            DeviceAgentCredential.device_id == device.id,
            DeviceAgentCredential.agent_type == "mikrotik_agent",
            DeviceAgentCredential.is_active.is_(True),
        )
    )
    if not credential or device.status != "online":
        return "agent_required"
    pending = db.scalar(
        select(DeviceJob.id).where(
            DeviceJob.device_id == device.id,
            DeviceJob.job_type == JOB_TYPE,
            DeviceJob.status.in_(["pending", "delivered"]),
        )
    )
    if pending:
        return "already_queued"
    job = DeviceJob(
        device_id=device.id,
        job_type=JOB_TYPE,
        payload={"read_only": True},
        expires_at=utcnow() + timedelta(minutes=10),
    )
    db.add(job)
    db.flush()
    core.add_event(
        db,
        "FIRMWARE_READINESS_QUEUED",
        actor=user,
        customer_id=device.customer_id,
        device_id=device.id,
        details={"job_id": str(job.id), "read_only": True},
        source="portal",
    )
    return "queued"


@router.post("/devices/{device_id}/firmware-readiness", name="queue_mikrotik_firmware_readiness")
def queue_firmware_readiness(
    request: Request,
    device_id: uuid.UUID,
    csrf: str = Form(...),
    return_to: str = Form("device"),
):
    core.validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "firmware.read")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        if device.vendor != "mikrotik":
            raise HTTPException(400, "Verifica RouterOS disponibile solo per MikroTik.")
        state = queue_readiness_job(db, device, user)
        db.commit()
    return _queue_redirect(device_id, return_to, state)


@router.post(
    "/api/v1/agents/mikrotik/firmware-readiness/{job_id}/complete",
    name="mikrotik_firmware_readiness_complete",
)
async def firmware_readiness_complete(request: Request, job_id: uuid.UUID):
    raw = await request.body()
    if len(raw) > MAX_RESULT_BODY:
        raise HTTPException(413, "Risultato firmware troppo grande.")
    payload = await agent_module._json_body(request)
    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type != JOB_TYPE:
            raise HTTPException(404, "Job firmware non trovato.")
        status = _clean(payload.get("status"), 30) or "failed"
        if status not in {"success", "failed"}:
            raise HTTPException(400, "Stato firmware non valido.")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        normalized = apply_firmware_readiness(db, device, result, source="mikrotik_agent") if status == "success" else {}
        job.status = status
        job.result = normalized if status == "success" else result
        job.last_error = _clean(payload.get("error"), 4000) or None
        job.completed_at = utcnow()
        core.add_event(
            db,
            "FIRMWARE_READINESS_COMPLETED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={"job_id": str(job.id), "status": status, "result": normalized},
            severity="warning" if status == "failed" else "info",
            result=status,
            source="mikrotik_agent",
        )
        db.commit()
    return {"status": "ok"}


def latest_firmware_readiness(db, device_id):
    return db.scalar(
        select(DeviceJob)
        .where(DeviceJob.device_id == device_id, DeviceJob.job_type == JOB_TYPE)
        .order_by(DeviceJob.created_at.desc())
        .limit(1)
    )


def install_mikrotik_firmware_readiness(app):
    _install_agent_handler()
    app.include_router(router)
