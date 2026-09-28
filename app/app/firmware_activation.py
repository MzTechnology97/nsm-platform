"""Safe RouterOS activation and post-reboot verification for Core 0.37.

The activation phase is deliberately separate from download staging. It is
available only to modern RouterOS agents after an approved plan, successful
off-device backup and successful package staging. The router reports that it is
about to reboot before `/system reboot` is executed. The worker then verifies a
fresh post-reboot heartbeat and the exact target RouterOS version.
"""
from __future__ import annotations

import uuid
from datetime import timedelta, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent_module
from app import mikrotik_legacy as legacy_module
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.firmware_upgrade_planner import _load_device, _modern_routeros
from app.models import ActionIssue, BackupRun, Device, Notification, utcnow

router = APIRouter()
JOB_TYPE = "firmware_activate"
ACTIVATION_START_TIMEOUT = timedelta(minutes=10)
POST_REBOOT_VERIFY_TIMEOUT = timedelta(minutes=12)
ISSUE_TITLE = "Aggiornamento RouterOS non verificato"

_HANDLER = r'''
    :if ($nsmJobType = "firmware_activate") do={
      :local nsmActivatePayload ($nsmJob->"payload")
      :local nsmTarget ($nsmActivatePayload->"target_version")
      :local nsmInstalled ""
      :local nsmLatest ""
      :local nsmStatus ""
      :local nsmActivateOk true
      :do {
        /system package update check-for-updates once
        :delay 3s
        :set nsmInstalled [/system package update get installed-version]
        :set nsmLatest [/system package update get latest-version]
        :set nsmStatus [/system package update get status]
        :if ($nsmInstalled = $nsmTarget) do={ :error "NSM target is already installed" }
        :if ($nsmLatest != $nsmTarget) do={ :error "NSM target changed after package staging" }
        :local nsmStartUrl ($nsmBase . "/api/v1/agents/mikrotik/firmware-activate/" . $nsmJobId . "/started")
        :local nsmStartBody [:serialize value={"installed_version"=$nsmInstalled;"latest_version"=$nsmLatest;"update_status"=$nsmStatus;"target_version"=$nsmTarget} to=json options=json.no-string-conversion]
        :local nsmStartResult [/tool fetch url=$nsmStartUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmStartBody output=user as-value]
        :if (($nsmStartResult->"status") != "finished") do={ :error "NSM activation acknowledgement failed" }
      } on-error={ :set nsmActivateOk false }
      :if ($nsmActivateOk) do={
        :log warning ("NSM approved RouterOS activation: rebooting for " . $nsmTarget)
        :delay 2s
        /system reboot
      } else={
        :local nsmFailUrl ($nsmBase . "/api/v1/agents/mikrotik/firmware-activate/" . $nsmJobId . "/failed")
        :do { /tool fetch url=$nsmFailUrl http-method=post http-header-field=$nsmHeaders http-data="{}" output=user as-value } on-error={}
      }
    }
'''


def _install_agent_handler():
    previous = agent_module._agent_source

    def source_with_activation(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        marker = '    :if ($nsmJobType = "firmware_stage") do={'
        if marker not in source:
            raise RuntimeError("MikroTik firmware activation extension point not found")
        return source.replace(marker, _HANDLER + "\n" + marker, 1)

    agent_module._agent_source = source_with_activation


def _install_adaptive_bootstrap_policy():
    """Keep 7.12 least-privilege while granting modern jobs required policies.

    PR #35/0.33 intentionally replaced the bootstrap with the bodyless legacy-
    compatible version. That bootstrap used `read,test` unconditionally, which
    is correct for 7.12 legacy transport but insufficient for modern backup and
    firmware jobs. The wrapper changes only local script installation policy;
    enrollment transport and wire format remain untouched.
    """
    original = legacy_module._legacy_bootstrap_script
    old = (
        '/system script add name="nsm-agent-heartbeat" policy=read,test source=$nsmAgentSource '
        'comment="NSM managed agent {agent_version}"\n'
        '/system scheduler add name="nsm-agent-heartbeat" interval=5m '
        'on-event="/system script run nsm-agent-heartbeat" policy=read,test comment="NSM managed agent"'
    )

    def adaptive(base_url: str, token: str):
        script = original(base_url, token)
        needle = old.format(agent_version=agent_module.AGENT_VERSION)
        if needle not in script:
            raise RuntimeError("RouterOS bodyless bootstrap policy marker not found")
        replacement = f''':local nsmFirstDot [:find $nsmVersionShort "."]
:local nsmVersionRest [:pick $nsmVersionShort ($nsmFirstDot + 1) [:len $nsmVersionShort]]
:local nsmSecondDot [:find $nsmVersionRest "."]
:local nsmMinorText $nsmVersionRest
:if ([:typeof $nsmSecondDot] != "nil") do={{ :set nsmMinorText [:pick $nsmVersionRest 0 $nsmSecondDot] }}
:local nsmMajor [:tonum [:pick $nsmVersionShort 0 $nsmFirstDot]]
:local nsmMinor [:tonum $nsmMinorText]
:local nsmModernPolicy false
:if ($nsmMajor > 7) do={{ :set nsmModernPolicy true }}
:if ($nsmMajor = 7) do={{ :if ($nsmMinor >= 13) do={{ :set nsmModernPolicy true }} }}
:if ($nsmModernPolicy) do={{
    /system script add name="nsm-agent-heartbeat" policy=read,write,test,sensitive,reboot source=$nsmAgentSource comment="NSM managed modern agent {agent_module.AGENT_VERSION}"
    /system scheduler add name="nsm-agent-heartbeat" interval=5m on-event="/system script run nsm-agent-heartbeat" policy=read,write,test,sensitive,reboot comment="NSM managed modern agent"
}} else={{
    /system script add name="nsm-agent-heartbeat" policy=read,test source=$nsmAgentSource comment="NSM managed legacy agent {agent_module.AGENT_VERSION}"
    /system scheduler add name="nsm-agent-heartbeat" interval=5m on-event="/system script run nsm-agent-heartbeat" policy=read,test comment="NSM managed legacy agent"
}}'''
        return script.replace(needle, replacement, 1)

    legacy_module._legacy_bootstrap_script = adaptive
    agent_module._bootstrap_script = adaptive


def _active_agent(db, device_id):
    return db.scalar(
        select(DeviceAgentCredential.id).where(
            DeviceAgentCredential.device_id == device_id,
            DeviceAgentCredential.agent_type == "mikrotik_agent",
            DeviceAgentCredential.is_active.is_(True),
        )
    ) is not None


def _validate_activation(db, device: Device, plan: FirmwareUpgradePlan | None):
    if not plan or plan.device_id != device.id:
        raise HTTPException(404, "Piano firmware non trovato.")
    if plan.status != "staged":
        raise HTTPException(409, "L'attivazione richiede pacchetti già staged.")
    if not _modern_routeros(device) or not _active_agent(db, device.id) or device.status != "online":
        raise HTTPException(409, "Attivazione disponibile solo su MikroTik online con agent moderno RouterOS 7.13+.")
    if not plan.backup_run_id:
        raise HTTPException(409, "Backup pre-upgrade mancante.")
    run = db.get(BackupRun, plan.backup_run_id)
    if not run or run.status != "success":
        raise HTTPException(409, "Backup pre-upgrade non valido.")
    staging = dict((plan.precheck_data or {}).get("staging") or {})
    if not staging.get("download_only") or staging.get("target_version") != plan.target_version:
        raise HTTPException(409, "Evidenza staging non valida o target non coerente.")
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
        _validate_activation(db, device, plan)
        expected = f"REBOOT {plan.target_version}"
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
        now = utcnow()
        job = DeviceJob(
            device_id=device.id,
            job_type=JOB_TYPE,
            payload={
                "plan_id": str(plan.id),
                "target_version": plan.target_version,
                "channel": plan.channel,
                "requires_reboot": True,
            },
        )
        db.add(job)
        db.flush()
        data = dict(plan.precheck_data or {})
        data["activation"] = {
            "queued_at": now.isoformat(),
            "job_id": str(job.id),
            "target_version": plan.target_version,
            "previous_version": device.firmware_version,
            "started_at": None,
            "verified_at": None,
        }
        plan.precheck_data = data
        plan.status = "executing"
        plan.started_at = plan.started_at or now
        plan.last_error = None
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
                "requires_reboot": True,
            },
            severity="warning",
            source="portal",
        )
        db.commit()
    return RedirectResponse(
        f"/devices/{device_id}/firmware-upgrade?plan={plan_id}&activation=queued",
        status_code=303,
    )


def _job_and_plan(db, device, job_id):
    job = db.get(DeviceJob, job_id)
    if not job or job.device_id != device.id or job.job_type != JOB_TYPE:
        raise HTTPException(404, "Job attivazione non trovato.")
    try:
        plan_id = uuid.UUID(str((job.payload or {}).get("plan_id") or ""))
    except ValueError:
        raise HTTPException(409, "Job attivazione privo di piano valido.")
    plan = db.get(FirmwareUpgradePlan, plan_id)
    if not plan or plan.device_id != device.id:
        raise HTTPException(409, "Piano attivazione non valido.")
    return job, plan


@router.post(
    "/api/v1/agents/mikrotik/firmware-activate/{job_id}/started",
    name="mikrotik_firmware_activation_started",
)
async def activation_started(request: Request, job_id: uuid.UUID):
    payload = await agent_module._json_body(request)
    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job, plan = _job_and_plan(db, device, job_id)
        if plan.status != "executing":
            raise HTTPException(409, "Piano non in esecuzione.")
        target = str(payload.get("target_version") or "").strip()
        latest = str(payload.get("latest_version") or "").strip()
        if target != plan.target_version or latest != plan.target_version:
            raise HTTPException(409, "Target RouterOS cambiato prima del reboot.")
        now = utcnow()
        data = dict(plan.precheck_data or {})
        activation = dict(data.get("activation") or {})
        activation.update(
            {
                "started_at": now.isoformat(),
                "agent_acknowledged": True,
                "observed_installed_version": payload.get("installed_version"),
                "observed_latest_version": latest,
                "update_status": payload.get("update_status"),
            }
        )
        data["activation"] = activation
        plan.precheck_data = data
        job.status = "running"
        job.result = {"phase": "reboot_acknowledged", "target_version": plan.target_version}
        core.add_event(
            db,
            "FIRMWARE_ACTIVATION_REBOOT_ACKNOWLEDGED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={"plan_id": str(plan.id), "job_id": str(job.id), "target_version": plan.target_version},
            severity="warning",
            source="mikrotik_agent",
        )
        db.commit()
    return {"status": "ok", "reboot_authorized": True}


@router.post(
    "/api/v1/agents/mikrotik/firmware-activate/{job_id}/failed",
    name="mikrotik_firmware_activation_failed",
)
def activation_failed(request: Request, job_id: uuid.UUID):
    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job, plan = _job_and_plan(db, device, job_id)
        now = utcnow()
        job.status = "failed"
        job.completed_at = now
        job.last_error = "RouterOS activation pre-reboot validation failed."
        plan.status = "failed"
        plan.completed_at = now
        plan.last_error = "Validazione immediatamente precedente al reboot non riuscita."
        core.add_event(
            db,
            "FIRMWARE_ACTIVATION_FAILED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={"plan_id": str(plan.id), "job_id": str(job.id), "phase": "pre_reboot"},
            severity="high",
            result="failed",
            source="mikrotik_agent",
        )
        db.commit()
    return {"status": "ok"}


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = __import__("datetime").datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _open_failure(db, device, plan, message):
    existing = db.scalar(
        select(ActionIssue).where(
            ActionIssue.device_id == device.id,
            ActionIssue.category == "firmware",
            ActionIssue.title == ISSUE_TITLE,
            ActionIssue.status.in_(["open", "acknowledged"]),
        )
    )
    if not existing:
        db.add(
            ActionIssue(
                category="firmware",
                severity="high",
                status="open",
                title=ISSUE_TITLE,
                details={"message": message, "plan_id": str(plan.id), "target_version": plan.target_version},
                customer_id=device.customer_id,
                device_id=device.id,
            )
        )
        db.add(
            Notification(
                severity="high",
                category="firmware",
                title=ISSUE_TITLE,
                message=message,
                customer_id=device.customer_id,
                device_id=device.id,
                source_url=f"/devices/{device.id}/firmware-upgrade?plan={plan.id}",
                is_active=True,
            )
        )


def _resolve_failure(db, device):
    issues = list(
        db.scalars(
            select(ActionIssue).where(
                ActionIssue.device_id == device.id,
                ActionIssue.category == "firmware",
                ActionIssue.title == ISSUE_TITLE,
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        )
    )
    now = utcnow()
    for issue in issues:
        issue.status = "resolved"
        issue.updated_at = now
        issue.resolved_at = now
    notifications = list(
        db.scalars(
            select(Notification).where(
                Notification.device_id == device.id,
                Notification.category == "firmware",
                Notification.title == ISSUE_TITLE,
                Notification.is_active.is_(True),
            )
        )
    )
    for notification in notifications:
        notification.is_active = False


def sync_firmware_activations(db, now):
    plans = list(db.scalars(select(FirmwareUpgradePlan).where(FirmwareUpgradePlan.status == "executing")))
    changes = 0
    for plan in plans:
        device = db.get(Device, plan.device_id)
        if not device:
            continue
        data = dict(plan.precheck_data or {})
        activation = dict(data.get("activation") or {})
        queued_at = _parse_time(activation.get("queued_at")) or plan.started_at
        started_at = _parse_time(activation.get("started_at"))
        job = None
        try:
            if activation.get("job_id"):
                job = db.get(DeviceJob, uuid.UUID(str(activation["job_id"])))
        except ValueError:
            job = None

        if started_at:
            last_seen = device.last_seen
            if last_seen and last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            if (
                str(device.firmware_version or "").strip() == plan.target_version
                and last_seen
                and last_seen > started_at
            ):
                plan.status = "success"
                plan.completed_at = now
                plan.last_error = None
                activation["verified_at"] = now.isoformat()
                activation["verified_version"] = device.firmware_version
                data["activation"] = activation
                plan.precheck_data = data
                if job:
                    job.status = "success"
                    job.completed_at = now
                    job.result = {"phase": "post_reboot_verified", "version": device.firmware_version}
                    job.last_error = None
                _resolve_failure(db, device)
                db.add(
                    Notification(
                        severity="info",
                        category="firmware",
                        title="Aggiornamento RouterOS verificato",
                        message=f"{device.display_name or device.name}: RouterOS {plan.target_version} operativo dopo il reboot.",
                        customer_id=device.customer_id,
                        device_id=device.id,
                        source_url=f"/devices/{device.id}/firmware-upgrade?plan={plan.id}",
                        is_active=True,
                    )
                )
                core.add_event(
                    db,
                    "FIRMWARE_ACTIVATION_VERIFIED",
                    customer_id=device.customer_id,
                    device_id=device.id,
                    details={"plan_id": str(plan.id), "target_version": plan.target_version, "verified_at": now.isoformat()},
                    source="worker",
                )
                changes += 1
                continue

            if now > started_at + POST_REBOOT_VERIFY_TIMEOUT:
                message = f"Il MikroTik non ha confermato RouterOS {plan.target_version} entro la finestra post-reboot."
                plan.status = "failed"
                plan.completed_at = now
                plan.last_error = message
                if job:
                    job.status = "failed"
                    job.completed_at = now
                    job.last_error = message
                _open_failure(db, device, plan, message)
                core.add_event(
                    db,
                    "FIRMWARE_ACTIVATION_VERIFICATION_FAILED",
                    customer_id=device.customer_id,
                    device_id=device.id,
                    details={"plan_id": str(plan.id), "target_version": plan.target_version, "phase": "post_reboot_timeout"},
                    severity="high",
                    result="failed",
                    source="worker",
                )
                changes += 1
                continue
        elif queued_at and now > queued_at + ACTIVATION_START_TIMEOUT:
            message = "Il job di attivazione RouterOS non è stato preso in carico dall'agent entro 10 minuti."
            plan.status = "failed"
            plan.completed_at = now
            plan.last_error = message
            if job:
                job.status = "failed"
                job.completed_at = now
                job.last_error = message
            _open_failure(db, device, plan, message)
            core.add_event(
                db,
                "FIRMWARE_ACTIVATION_VERIFICATION_FAILED",
                customer_id=device.customer_id,
                device_id=device.id,
                details={"plan_id": str(plan.id), "target_version": plan.target_version, "phase": "agent_start_timeout"},
                severity="high",
                result="failed",
                source="worker",
            )
            changes += 1
    return changes


def firmware_activation_tick(now=None):
    now = (now or utcnow()).astimezone(timezone.utc)
    with SessionLocal() as db:
        changes = sync_firmware_activations(db, now)
        db.commit()
        return {"firmware_activation_changes": changes}


def install_firmware_activation(app):
    _install_agent_handler()
    _install_adaptive_bootstrap_policy()
    app.include_router(router)
