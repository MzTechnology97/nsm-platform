"""Safe staged RouterOS activation and post-reboot verification for Core 0.37.

Core 0.32 already downloads the approved RouterOS packages.  This module adds a
third explicit operator gate before reboot, records an agent acknowledgement
*before* the router restarts, and only marks the upgrade successful after a
subsequent inventory heartbeat reports the approved target version.

RouterBOOT is deliberately not upgraded here.  It remains a separate action so
one RouterOS upgrade cannot silently turn into a second firmware operation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent_module
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.firmware_plan_recovery import ACTIVATION_JOB_TTL
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.firmware_upgrade_planner import _load_device, _modern_routeros
from app.models import BackupRun, Device, utcnow

router = APIRouter()
JOB_TYPE = "firmware_activate"
MAX_RESULT_BODY = 64 * 1024
VERIFICATION_TIMEOUT = timedelta(minutes=20)
MISMATCH_GRACE = timedelta(minutes=5)

_HANDLER = r'''
    :if ($nsmJobType = "firmware_activate") do={
      :local nsmActivatePayload ($nsmJob->"payload")
      :local nsmTarget ($nsmActivatePayload->"target_version")
      :local nsmActivateOk true
      :local nsmActivateError ""
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
      } on-error={
        :set nsmActivateOk false
        :set nsmActivateError "RouterOS activation preflight failed or target changed"
      }
      :local nsmAckUrl ($nsmBase . "/api/v1/agents/mikrotik/firmware-activate/" . $nsmJobId . "/ack")
      :local nsmAckStatus "failed"
      :if ($nsmActivateOk) do={ :set nsmAckStatus "accepted" }
      :local nsmAckBody [:serialize value={"status"=$nsmAckStatus;"error"=$nsmActivateError;"result"={"installed_version"=$nsmInstalled;"latest_version"=$nsmLatest;"update_status"=$nsmUpdateStatus;"target_version"=$nsmTarget;"reboot_required"=true}} to=json options=json.no-string-conversion]
      :local nsmAckResult {}
      :do {
        :set nsmAckResult [/tool fetch url=$nsmAckUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmAckBody output=user as-value]
      } on-error={
        :set nsmActivateOk false
        :log warning "NSM firmware activation acknowledgement failed; reboot cancelled"
      }
      :if ($nsmActivateOk) do={
        :if (($nsmAckResult->"status") = "finished") do={
          :delay 2s
          /system reboot
        } else={
          :log warning "NSM firmware activation acknowledgement not accepted; reboot cancelled"
        }
      }
    }
'''


def _install_agent_handler():
    previous = agent_module._agent_source

    def source_with_activation(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        marker = '    :if ($nsmJobType = "backup_mikrotik") do={'
        if marker not in source:
            raise RuntimeError("MikroTik firmware activation extension point not found")
        return source.replace(marker, _HANDLER + "\n" + marker, 1)

    agent_module._agent_source = source_with_activation


def _active_agent(db, device_id):
    return db.scalar(
        select(DeviceAgentCredential.id).where(
            DeviceAgentCredential.device_id == device_id,
            DeviceAgentCredential.agent_type == "mikrotik_agent",
            DeviceAgentCredential.is_active.is_(True),
        )
    ) is not None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _activation_data(plan: FirmwareUpgradePlan) -> dict:
    return dict((plan.precheck_data or {}).get("activation") or {})


def _activation_accepted_at(plan: FirmwareUpgradePlan) -> datetime | None:
    raw = _activation_data(plan).get("accepted_at")
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return _as_utc(value)


def _validate_plan_for_activation(db, device: Device, plan: FirmwareUpgradePlan | None):
    if not plan or plan.device_id != device.id:
        raise HTTPException(404, "Piano firmware non trovato.")
    if plan.status != "staged":
        raise HTTPException(409, "Il piano deve essere in stato staged prima dell'attivazione.")
    if not _modern_routeros(device) or not _active_agent(db, device.id) or device.status != "online":
        raise HTTPException(409, "Attivazione disponibile solo su MikroTik online con agent moderno RouterOS 7.13+.")
    if not plan.backup_run_id:
        raise HTTPException(409, "Backup pre-upgrade mancante.")
    run = db.get(BackupRun, plan.backup_run_id)
    if not run or run.status != "success":
        raise HTTPException(409, "Il backup pre-upgrade non è completato con successo.")
    staging = dict((plan.precheck_data or {}).get("staging") or {})
    if not staging or not staging.get("download_only"):
        raise HTTPException(409, "Evidenza di staging RouterOS mancante.")
    if str(staging.get("target_version") or "").strip() != str(plan.target_version).strip():
        raise HTTPException(409, "Il target staged non coincide con il piano approvato.")
    if str(device.firmware_version or "").strip() == plan.target_version:
        raise HTTPException(409, "La versione target risulta già installata.")
    return run


@router.post(
    "/devices/{device_id}/firmware-upgrade/{plan_id}/activate",
    name="activate_firmware_upgrade_plan",
)
def activate_plan(
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
        _validate_plan_for_activation(db, device, plan)

        expected = f"ACTIVATE {plan.target_version}"
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
            raise HTTPException(409, "Attivazione firmware già in corso.")

        job = DeviceJob(
            device_id=device.id,
            job_type=JOB_TYPE,
            expires_at=utcnow() + ACTIVATION_JOB_TTL,
            payload={
                "plan_id": str(plan.id),
                "target_version": plan.target_version,
                "channel": plan.channel,
                "staged": True,
                "action": "reboot_to_activate_staged_packages",
            },
        )
        db.add(job)
        db.flush()
        plan.status = "activation_pending"
        plan.last_error = None
        data = dict(plan.precheck_data or {})
        data["activation"] = {
            "queued_at": utcnow().isoformat(),
            "job_id": str(job.id),
            "target_version": plan.target_version,
            "routerboot_upgrade": False,
        }
        plan.precheck_data = data
        core.add_event(
            db,
            "FIRMWARE_ACTIVATION_QUEUED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={
                "plan_id": str(plan.id),
                "job_id": str(job.id),
                "target_version": plan.target_version,
                "routerboot_upgrade": False,
            },
            severity="warning",
            source="portal",
        )
        db.commit()

    return RedirectResponse(
        f"/devices/{device_id}/firmware-upgrade?plan={plan_id}&activation=queued",
        status_code=303,
    )


@router.post(
    "/api/v1/agents/mikrotik/firmware-activate/{job_id}/ack",
    name="mikrotik_firmware_activation_ack",
)
async def activation_ack(request: Request, job_id: uuid.UUID):
    raw = await request.body()
    if len(raw) > MAX_RESULT_BODY:
        raise HTTPException(413, "Risultato attivazione troppo grande.")
    payload = await agent_module._json_body(request)

    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type != JOB_TYPE:
            raise HTTPException(404, "Job attivazione non trovato.")
        if job.status not in {"delivered", "running", "pending"}:
            raise HTTPException(409, "Job attivazione già finalizzato.")
        try:
            plan_id = uuid.UUID(str((job.payload or {}).get("plan_id") or ""))
        except ValueError:
            raise HTTPException(409, "Job attivazione privo di piano valido.")
        plan = db.get(FirmwareUpgradePlan, plan_id)
        if not plan or plan.device_id != device.id:
            raise HTTPException(409, "Piano attivazione non valido.")
        if plan.status != "activation_pending":
            raise HTTPException(409, "Il piano non è in attesa di attivazione.")

        status = str(payload.get("status") or "failed").strip().lower()
        if status not in {"accepted", "failed"}:
            raise HTTPException(400, "Stato attivazione non valido.")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        error = str(payload.get("error") or "").strip()[:4000] or None
        now = utcnow()
        job.result = result
        job.completed_at = now

        data = dict(plan.precheck_data or {})
        activation = dict(data.get("activation") or {})
        activation.update(
            {
                "acknowledged_at": now.isoformat(),
                "agent_result": result,
                "agent_status": status,
            }
        )

        if status == "accepted":
            job.status = "success"
            job.last_error = None
            plan.status = "reboot_pending"
            plan.last_error = None
            activation["accepted_at"] = now.isoformat()
            activation["heartbeat_baseline_at"] = (
                _as_utc(device.inventory_last_verified_at).isoformat()
                if device.inventory_last_verified_at
                else None
            )
            event_type = "FIRMWARE_ACTIVATION_ACCEPTED"
            severity = "warning"
            result_status = "success"
        else:
            job.status = "failed"
            job.last_error = error or "Preflight attivazione RouterOS non riuscito."
            plan.status = "failed"
            plan.last_error = job.last_error
            plan.completed_at = now
            activation["failed_at"] = now.isoformat()
            event_type = "FIRMWARE_ACTIVATION_REJECTED"
            severity = "warning"
            result_status = "failed"

        data["activation"] = activation
        plan.precheck_data = data
        core.add_event(
            db,
            event_type,
            customer_id=device.customer_id,
            device_id=device.id,
            details={
                "plan_id": str(plan.id),
                "job_id": str(job.id),
                "target_version": plan.target_version,
                "agent_status": status,
                "reboot_expected": status == "accepted",
            },
            severity=severity,
            result=result_status,
            source="mikrotik_agent",
        )
        db.commit()
    return {"status": "ok"}


def reconcile_firmware_activations(now: datetime | None = None) -> dict[str, int]:
    """Resolve reboot-pending plans only from post-ack inventory evidence."""
    now = _as_utc(now or utcnow())
    stats = {"checked": 0, "success": 0, "failed": 0, "pending": 0}
    with SessionLocal() as db:
        plans = list(
            db.scalars(
                select(FirmwareUpgradePlan).where(FirmwareUpgradePlan.status == "reboot_pending")
            )
        )
        for plan in plans:
            stats["checked"] += 1
            device = db.get(Device, plan.device_id)
            accepted_at = _activation_accepted_at(plan)
            if not device or not accepted_at:
                plan.status = "failed"
                plan.last_error = "Evidenza di attivazione incompleta: impossibile verificare il reboot."
                plan.completed_at = now
                stats["failed"] += 1
                continue

            observed_at = _as_utc(device.inventory_last_verified_at)
            observed_version = str(device.firmware_version or "").strip()
            post_reboot_evidence = bool(observed_at and observed_at > accepted_at)

            if post_reboot_evidence and observed_version == str(plan.target_version).strip():
                plan.status = "success"
                plan.completed_at = now
                plan.last_error = None
                data = dict(plan.precheck_data or {})
                activation = dict(data.get("activation") or {})
                activation["verified_at"] = now.isoformat()
                activation["observed_at"] = observed_at.isoformat()
                activation["observed_version"] = observed_version
                activation["verification"] = "target_version_confirmed"
                data["activation"] = activation
                plan.precheck_data = data
                core.add_event(
                    db,
                    "FIRMWARE_UPGRADE_VERIFIED",
                    customer_id=device.customer_id,
                    device_id=device.id,
                    details={
                        "plan_id": str(plan.id),
                        "target_version": plan.target_version,
                        "observed_version": observed_version,
                        "observed_at": observed_at.isoformat(),
                    },
                    source="worker",
                )
                stats["success"] += 1
                continue

            age = now - accepted_at
            if post_reboot_evidence and age >= MISMATCH_GRACE:
                plan.status = "failed"
                plan.completed_at = now
                plan.last_error = (
                    f"Il MikroTik è tornato online ma riporta RouterOS {observed_version or 'sconosciuto'}, "
                    f"non il target {plan.target_version}."
                )
                data = dict(plan.precheck_data or {})
                activation = dict(data.get("activation") or {})
                activation["failed_at"] = now.isoformat()
                activation["observed_at"] = observed_at.isoformat() if observed_at else None
                activation["observed_version"] = observed_version or None
                activation["verification"] = "version_mismatch"
                data["activation"] = activation
                plan.precheck_data = data
                core.add_event(
                    db,
                    "FIRMWARE_UPGRADE_VERIFICATION_FAILED",
                    customer_id=device.customer_id,
                    device_id=device.id,
                    details={
                        "plan_id": str(plan.id),
                        "target_version": plan.target_version,
                        "observed_version": observed_version or None,
                        "reason": "version_mismatch",
                    },
                    severity="warning",
                    result="failed",
                    source="worker",
                )
                stats["failed"] += 1
                continue

            if age >= VERIFICATION_TIMEOUT:
                plan.status = "failed"
                plan.completed_at = now
                plan.last_error = "Timeout: nessun heartbeat post-reboot valido entro 20 minuti."
                data = dict(plan.precheck_data or {})
                activation = dict(data.get("activation") or {})
                activation["failed_at"] = now.isoformat()
                activation["verification"] = "heartbeat_timeout"
                data["activation"] = activation
                plan.precheck_data = data
                core.add_event(
                    db,
                    "FIRMWARE_UPGRADE_VERIFICATION_FAILED",
                    customer_id=device.customer_id,
                    device_id=device.id,
                    details={
                        "plan_id": str(plan.id),
                        "target_version": plan.target_version,
                        "reason": "heartbeat_timeout",
                    },
                    severity="warning",
                    result="failed",
                    source="worker",
                )
                stats["failed"] += 1
                continue

            stats["pending"] += 1

        db.commit()
    return stats


def install_firmware_activation(app):
    _install_agent_handler()
    app.include_router(router)
