"""Approved RouterOS package staging for Core 0.32.

This is deliberately a download-only phase.  It never calls `install`, never
upgrades RouterBOOT, and never reboots the router.  The approved target is
revalidated on the device immediately before download.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent_module
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.firmware_upgrade_planner import _load_device, _modern_routeros
from app.models import BackupRun, utcnow

router = APIRouter()
JOB_TYPE = "firmware_stage"
MAX_RESULT_BODY = 64 * 1024

_HANDLER = r'''
    :if ($nsmJobType = "firmware_stage") do={
      :local nsmStagePayload ($nsmJob->"payload")
      :local nsmTarget ($nsmStagePayload->"target_version")
      :local nsmStageOk true
      :local nsmStageError ""
      :local nsmInstalled ""
      :local nsmLatest ""
      :local nsmUpdateStatus ""
      :do {
        /system package update check-for-updates once
        :delay 3s
        :set nsmInstalled [/system package update get installed-version]
        :set nsmLatest [/system package update get latest-version]
        :set nsmUpdateStatus [/system package update get status]
        :if ($nsmInstalled = $nsmTarget) do={ :error "NSM target is already installed" }
        :if ($nsmLatest != $nsmTarget) do={ :error "NSM approved target no longer matches RouterOS latest-version" }
        /system package update download
      } on-error={ :set nsmStageOk false; :set nsmStageError "RouterOS package staging failed or target changed" }
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/firmware-stage/" . $nsmJobId . "/complete")
      :local nsmDoneStatus "failed"
      :if ($nsmStageOk) do={ :set nsmDoneStatus "success" }
      :local nsmDoneBody [:serialize value={"status"=$nsmDoneStatus;"error"=$nsmStageError;"result"={"installed_version"=$nsmInstalled;"latest_version"=$nsmLatest;"update_status"=$nsmUpdateStatus;"target_version"=$nsmTarget;"download_only"=true}} to=json options=json.no-string-conversion]
      :do { /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmDoneBody output=user as-value } on-error={ :log warning "NSM firmware staging report failed" }
    }
'''


def _install_agent_handler():
    previous = agent_module._agent_source

    def source_with_staging(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        marker = '    :if ($nsmJobType = "backup_mikrotik") do={'
        if marker not in source:
            raise RuntimeError("MikroTik firmware staging extension point not found")
        return source.replace(marker, _HANDLER + "\n" + marker, 1)

    agent_module._agent_source = source_with_staging


def _active_agent(db, device_id):
    return db.scalar(
        select(DeviceAgentCredential.id).where(
            DeviceAgentCredential.device_id == device_id,
            DeviceAgentCredential.agent_type == "mikrotik_agent",
            DeviceAgentCredential.is_active.is_(True),
        )
    ) is not None


def _validate_plan_for_staging(db, device, plan):
    if not plan or plan.device_id != device.id:
        raise HTTPException(404, "Piano firmware non trovato.")
    if plan.status != "approved":
        raise HTTPException(409, "Il piano deve essere approvato prima dello staging.")
    if not _modern_routeros(device) or not _active_agent(db, device.id) or device.status != "online":
        raise HTTPException(409, "Staging disponibile solo su MikroTik online con agent moderno RouterOS 7.13+.")
    if not plan.backup_run_id:
        raise HTTPException(409, "Backup pre-upgrade mancante.")
    run = db.get(BackupRun, plan.backup_run_id)
    if not run or run.status != "success":
        raise HTTPException(409, "Il backup pre-upgrade non è completato con successo.")
    if str(device.firmware_version or "").strip() == plan.target_version:
        raise HTTPException(409, "La versione target risulta già installata.")
    return run


@router.post(
    "/devices/{device_id}/firmware-upgrade/{plan_id}/stage",
    name="stage_firmware_upgrade_plan",
)
def stage_plan(
    request: Request,
    device_id: uuid.UUID,
    plan_id: uuid.UUID,
    csrf: str = Form(...),
    confirmation: str = Form(...),
):
    core.validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "firmware.execute")
        device = _load_device(db, device_id)
        plan = db.get(FirmwareUpgradePlan, plan_id)
        _validate_plan_for_staging(db, device, plan)
        expected = f"DOWNLOAD {plan.target_version}"
        if confirmation.strip() != expected:
            raise HTTPException(400, f"Conferma non valida. Inserisci esattamente: {expected}")
        existing = db.scalar(
            select(DeviceJob.id).where(
                DeviceJob.device_id == device.id,
                DeviceJob.job_type == JOB_TYPE,
                DeviceJob.status.in_(["pending", "delivered", "running"]),
            )
        )
        if existing:
            raise HTTPException(409, "Staging firmware già in corso.")
        job = DeviceJob(
            device_id=device.id,
            job_type=JOB_TYPE,
            payload={
                "plan_id": str(plan.id),
                "target_version": plan.target_version,
                "channel": plan.channel,
                "download_only": True,
            },
        )
        db.add(job)
        db.flush()
        plan.status = "staging"
        plan.started_at = utcnow()
        plan.last_error = None
        core.add_event(
            db,
            "FIRMWARE_PACKAGE_STAGING_QUEUED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={
                "plan_id": str(plan.id),
                "job_id": str(job.id),
                "target_version": plan.target_version,
                "download_only": True,
            },
            severity="warning",
            source="portal",
        )
        db.commit()
    return RedirectResponse(
        f"/devices/{device_id}/firmware-upgrade?plan={plan_id}&staging=queued",
        status_code=303,
    )


@router.post(
    "/api/v1/agents/mikrotik/firmware-stage/{job_id}/complete",
    name="mikrotik_firmware_stage_complete",
)
async def stage_complete(request: Request, job_id: uuid.UUID):
    raw = await request.body()
    if len(raw) > MAX_RESULT_BODY:
        raise HTTPException(413, "Risultato staging troppo grande.")
    payload = await agent_module._json_body(request)
    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type != JOB_TYPE:
            raise HTTPException(404, "Job staging non trovato.")
        try:
            plan_id = uuid.UUID(str((job.payload or {}).get("plan_id") or ""))
        except ValueError:
            raise HTTPException(409, "Job staging privo di piano valido.")
        plan = db.get(FirmwareUpgradePlan, plan_id)
        if not plan or plan.device_id != device.id:
            raise HTTPException(409, "Piano staging non valido.")
        status = str(payload.get("status") or "failed").strip().lower()
        if status not in {"success", "failed"}:
            raise HTTPException(400, "Stato staging non valido.")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        error = str(payload.get("error") or "").strip()[:4000] or None
        job.status = status
        job.result = result
        job.last_error = error
        job.completed_at = utcnow()
        if status == "success":
            plan.status = "staged"
            plan.last_error = None
            data = dict(plan.precheck_data or {})
            data["staging"] = {
                "completed_at": utcnow().isoformat(),
                "job_id": str(job.id),
                "target_version": plan.target_version,
                "download_only": True,
                "result": result,
            }
            plan.precheck_data = data
        else:
            plan.status = "failed"
            plan.last_error = error or "Download pacchetti RouterOS non riuscito."
        core.add_event(
            db,
            "FIRMWARE_PACKAGE_STAGING_COMPLETED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={
                "plan_id": str(plan.id),
                "job_id": str(job.id),
                "status": status,
                "target_version": plan.target_version,
                "download_only": True,
            },
            severity="warning" if status == "failed" else "info",
            result=status,
            source="mikrotik_agent",
        )
        db.commit()
    return {"status": "ok"}


def install_firmware_package_staging(app):
    _install_agent_handler()
    app.include_router(router)
