"""Approved RouterOS package staging for Core 0.32.

This is deliberately a download-only phase.  It never calls `install`, never
upgrades RouterBOOT, and never reboots the router.  The approved target is
revalidated on the device immediately before download.

Agent 0.49.25: ``/system package update download`` may return before the
package is written (or fail for lack of flash space), so the Agent waits up to
``DOWNLOAD_WAIT_SECONDS`` for RouterOS to report the download and looks for the
``routeros-<target>-*.npk`` file.  Only a verified package (``package_verified``)
marks the plan *staged*; otherwise the plan fails with what RouterOS reported and
the free flash space.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent_module
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.firmware_plan_recovery import STAGING_JOB_TTL
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.firmware_upgrade_planner import _modern_routeros
from app.models import BackupRun, utcnow

router = APIRouter()
JOB_TYPE = "firmware_stage"
MAX_RESULT_BODY = 64 * 1024
DOWNLOAD_WAIT_SECONDS = 180  # 36 checks, 5 s apart (see _HANDLER)

_HANDLER = r'''
    :if ($nsmJobType = "firmware_stage") do={
      :local nsmStagePayload ($nsmJob->"payload")
      :local nsmTarget ($nsmStagePayload->"target_version")
      :local nsmStageOk true
      :local nsmStageError ""
      :local nsmInstalled ""
      :local nsmLatest ""
      :local nsmUpdateStatus ""
      :local nsmStep "check"
      :local nsmFreeBefore ""
      :local nsmFreeAfter ""
      :local nsmPackageFile ""
      :local nsmPackageSize ""
      :local nsmVerified false
      :do {
        /system package update check-for-updates once
        :delay 3s
        :set nsmInstalled [/system package update get installed-version]
        :set nsmLatest [/system package update get latest-version]
        :set nsmUpdateStatus [/system package update get status]
        :if ($nsmInstalled = $nsmTarget) do={ :error "NSM target is already installed" }
        :if ($nsmLatest != $nsmTarget) do={ :error "NSM approved target no longer matches RouterOS latest-version" }
        :set nsmFreeBefore [/system resource get free-hdd-space]
        :set nsmStep "download"
        :do { /system package update download } on-error={ :set nsmStep "download-command" }
        :local nsmWait 0
        :while (!$nsmVerified && $nsmWait < 36) do={
          :delay 5s
          :set nsmWait ($nsmWait + 1)
          :do { :set nsmUpdateStatus [/system package update get status] } on-error={}
          :foreach nsmF in=[/file find where name~("routeros-" . $nsmTarget . "-")] do={
            :set nsmPackageFile [/file get $nsmF name]
            :set nsmPackageSize [/file get $nsmF size]
          }
          :if ($nsmUpdateStatus ~ "Downloaded" || [:len $nsmPackageFile] > 0) do={ :set nsmVerified true }
          :if ($nsmUpdateStatus ~ "(ERROR|rror|ailed|space)") do={ :set nsmWait 36 }
        }
        :do { :set nsmFreeAfter [/system resource get free-hdd-space] } on-error={}
        :if (!$nsmVerified) do={ :set nsmStep "verify"; :error "NSM package not downloaded" }
      } on-error={ :set nsmStageOk false; :set nsmStageError ("RouterOS package staging failed at " . $nsmStep . ": " . $nsmUpdateStatus) }
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/firmware-stage/" . $nsmJobId . "/complete")
      :local nsmDoneStatus "failed"
      :if ($nsmStageOk) do={ :set nsmDoneStatus "success" }
      :local nsmDoneBody [:serialize value={"status"=$nsmDoneStatus;"error"=$nsmStageError;"result"={"installed_version"=$nsmInstalled;"latest_version"=$nsmLatest;"update_status"=$nsmUpdateStatus;"target_version"=$nsmTarget;"download_only"=true;"step"=$nsmStep;"package_verified"=$nsmVerified;"package_file"=$nsmPackageFile;"package_size"=$nsmPackageSize;"free_hdd_before"=$nsmFreeBefore;"free_hdd_after"=$nsmFreeAfter}} to=json options=json.no-string-conversion]
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


def _number(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def failure_message(target: str, result: dict, agent_error: str | None) -> str:
    """What the operator reads on the plan when the package did not reach the router."""
    status = str(result.get("update_status") or "").strip()
    free = _number(result.get("free_hdd_after")) or _number(result.get("free_hdd_before"))
    if "package_verified" not in result:
        return ("Download del pacchetto RouterOS non verificato: l'Agent installato non controlla se il pacchetto è arrivato. "
                "Aggiorna l'Agent (scheda Agent) e ripeti il download.")
    parts = [f"Il pacchetto RouterOS {target} non risulta scaricato sul router"]
    if result.get("step") == "check":
        parts = [f"Controllo prima del download non riuscito (versione installata o disponibile cambiata) per RouterOS {target}"]
    if status:
        parts.append(f"RouterOS riporta «{status}»")
    if free is not None:
        parts.append(f"spazio libero {free / 1048576:.1f} MB")
    message = ", ".join(parts) + "."
    if free is not None and free < 16 * 1048576 and result.get("step") != "check":
        message += (" Su apparati con poca flash (es. hEX, 16 MB) il pacchetto può non entrarci: libera spazio nella cartella Files "
                    "(backup, export, pacchetti vecchi) e ripeti il download.")
    if agent_error and not status:
        message += f" Dettaglio Agent: {agent_error}"
    return message[:4000]


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
        if status == "success" and result.get("package_verified") is not True:
            # Agents before 0.49.25 reported success right after starting the download.
            status = "failed"
        if status == "failed":
            error = failure_message(plan.target_version, result, error)
        job.status = status
        job.result = result
        job.last_error = error
        job.completed_at = utcnow()
        # A cancelled/expired plan keeps its terminal state; the late report
        # remains visible on the job and in the audit event only.
        plan_updated = plan.status == "staging"
        if plan_updated and status == "success":
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
        elif plan_updated:
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
                "plan_updated": plan_updated,
                "plan_status": plan.status,
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


def stage_record(db, user, device: Device, plan_id: uuid.UUID, confirmation: str) -> DeviceJob:
    """Queue the RouterOS package download (no reboot) of an approved plan after ``DOWNLOAD <target>``; audited, commits."""
    plan = db.get(FirmwareUpgradePlan, plan_id)
    _validate_plan_for_staging(db, device, plan)
    expected = f"DOWNLOAD {plan.target_version}"
    if (confirmation or "").strip() != expected:
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
        expires_at=utcnow() + STAGING_JOB_TTL,
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
    return job
