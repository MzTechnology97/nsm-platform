"""Firmware upgrade planning and pre-backup gate for Core 0.31.

No RouterOS install/download/reboot command is implemented here.  This module
only creates an auditable plan, queues the mandatory off-device backup through
the existing modern MikroTik backup transport, and permits explicit operator
approval after that backup succeeds.
"""
from __future__ import annotations

import re
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import main as core
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_core import _effective_policy, _policy_settings
from app.backup_models import BackupPolicySettings
from app.db import SessionLocal
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.mikrotik_backup import _backup_formats
from app.mikrotik_backup_models import MikrotikBackupJobSecret
from app.models import BackupPolicy, BackupRun, Device, utcnow
from app.secret_vault import encrypt_text

router = APIRouter()
ACTIVE_STATES = ("backup_pending", "ready", "approved")
_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)")
READINESS_MAX_AGE = timedelta(hours=6)


def _modern_routeros(device: Device) -> bool:
    match = _VERSION_RE.match(str(device.firmware_version or ""))
    if not match or (int(match.group(1)), int(match.group(2))) < (7, 13):
        return False
    data = dict(device.inventory_data or {})
    transport = str(data.get("agent_transport") or "").strip().lower()
    agent_version = str(data.get("agent_version") or "").strip().lower()
    if transport == "legacy" or agent_version.endswith("-legacy"):
        return False
    return True


def _readiness(device: Device) -> dict:
    return dict((device.inventory_data or {}).get("firmware_readiness") or {})


def _readiness_fresh(device: Device) -> bool:
    raw = _readiness(device).get("checked_at")
    if not raw:
        return False
    try:
        checked = __import__("datetime").datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=utcnow().tzinfo)
    return utcnow() - checked <= READINESS_MAX_AGE


def _effective_policy_and_settings(db, device):
    policies = list(db.scalars(select(BackupPolicy).where(BackupPolicy.is_enabled.is_(True))))
    settings_map = {policy.id: _policy_settings(db, policy, create=True) for policy in policies}
    policy = _effective_policy(device, policies, settings_map)
    return policy, settings_map.get(policy.id) if policy else None


def queue_preupgrade_backup(db, device: Device, actor, plan: FirmwareUpgradePlan):
    credential = db.scalar(
        select(DeviceAgentCredential).where(
            DeviceAgentCredential.device_id == device.id,
            DeviceAgentCredential.agent_type == "mikrotik_agent",
            DeviceAgentCredential.is_active.is_(True),
        )
    )
    if not credential:
        raise HTTPException(409, "Agent MikroTik non autenticato.")
    pending = db.scalar(
        select(DeviceJob.id).where(
            DeviceJob.device_id == device.id,
            DeviceJob.job_type == "backup_mikrotik",
            DeviceJob.status.in_(["pending", "delivered", "running"]),
        )
    )
    if pending:
        raise HTTPException(409, "Esiste già un backup MikroTik in corso.")

    policy, settings = _effective_policy_and_settings(db, device)
    if not policy:
        raise HTTPException(409, "Nessuna backup policy effettiva per questo apparato.")
    formats = _backup_formats(settings)
    if not formats:
        raise HTTPException(409, "La policy non abilita alcun formato MikroTik.")

    run = BackupRun(
        device_id=device.id,
        policy_id=policy.id,
        status="pending",
        backup_type="mikrotik_multi" if len(formats) > 1 else formats[0],
    )
    db.add(run)
    db.flush()
    job = DeviceJob(
        device_id=device.id,
        job_type="backup_mikrotik",
        status="pending",
        payload={
            "run_id": str(run.id),
            "formats": formats,
            "cleanup_router_files": True,
            "reason": "pre_firmware_upgrade",
            "firmware_upgrade_plan_id": str(plan.id),
        },
    )
    db.add(job)
    db.flush()
    db.add(
        MikrotikBackupJobSecret(
            job_id=job.id,
            encrypted_backup_password=encrypt_text(secrets.token_urlsafe(24)),
        )
    )
    plan.backup_run_id = run.id
    plan.status = "backup_pending"
    core.add_event(
        db,
        "FIRMWARE_PREUPGRADE_BACKUP_QUEUED",
        actor=actor,
        customer_id=device.customer_id,
        device_id=device.id,
        details={
            "plan_id": str(plan.id),
            "backup_run_id": str(run.id),
            "backup_job_id": str(job.id),
            "formats": formats,
        },
        source="portal",
    )
    return run, job


def sync_plan_status(db, plan: FirmwareUpgradePlan):
    if plan.status not in {"backup_pending", "ready"} or not plan.backup_run_id:
        return plan
    run = db.get(BackupRun, plan.backup_run_id)
    if not run:
        plan.status = "failed"
        plan.last_error = "Backup pre-upgrade non trovato."
        return plan
    if run.status == "success":
        plan.status = "ready"
        plan.ready_at = plan.ready_at or utcnow()
        plan.last_error = None
    elif run.status in {"failed", "cancelled"}:
        plan.status = "failed"
        plan.last_error = "Backup pre-upgrade non riuscito: piano bloccato."
    return plan


def _load_device(db, device_id):
    device = db.scalar(
        select(Device)
        .where(Device.id == device_id)
        .options(selectinload(Device.customer), selectinload(Device.site))
    )
    if not device:
        raise HTTPException(404)
    return device


@router.post("/devices/{device_id}/firmware-upgrade/plan", name="create_firmware_upgrade_plan")
def create_plan(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
    core.validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "firmware.execute")
        device = _load_device(db, device_id)
        if device.vendor != "mikrotik":
            raise HTTPException(400, "Piano firmware disponibile solo per MikroTik in questa fase.")
        if device.status != "online":
            raise HTTPException(409, "Il MikroTik deve essere online.")
        if not _modern_routeros(device):
            raise HTTPException(409, "Upgrade remoto sicuro richiede RouterOS 7.13+ con agent moderno.")
        if not _readiness_fresh(device):
            raise HTTPException(409, "Esegui prima una verifica firmware recente (massimo 6 ore).")
        readiness = _readiness(device)
        target = str(readiness.get("latest_version") or device.recommended_firmware_version or "").strip()
        if not target or target == str(device.firmware_version or "").strip():
            raise HTTPException(409, "Nessun aggiornamento RouterOS disponibile.")
        existing = db.scalar(
            select(FirmwareUpgradePlan.id).where(
                FirmwareUpgradePlan.device_id == device.id,
                FirmwareUpgradePlan.status.in_(ACTIVE_STATES),
            )
        )
        if existing:
            return RedirectResponse(f"/devices/{device.id}/firmware-upgrade?plan={existing}", status_code=303)

        plan = FirmwareUpgradePlan(
            device_id=device.id,
            target_version=target[:150],
            channel=str(readiness.get("channel") or "")[:40] or None,
            status="draft",
            created_by=user.id,
            precheck_data={
                "installed_version": device.firmware_version,
                "target_version": target,
                "channel": readiness.get("channel"),
                "free_hdd_space": readiness.get("free_hdd_space"),
                "routerboard_current": readiness.get("routerboard_current"),
                "routerboard_upgrade": readiness.get("routerboard_upgrade"),
                "readiness_checked_at": readiness.get("checked_at"),
                "agent_transport": (device.inventory_data or {}).get("agent_transport") or "modern-inferred",
            },
        )
        db.add(plan)
        db.flush()
        queue_preupgrade_backup(db, device, user, plan)
        core.add_event(
            db,
            "FIRMWARE_UPGRADE_PLAN_CREATED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={"plan_id": str(plan.id), "target_version": target, "status": plan.status},
            source="portal",
        )
        db.commit()
        return RedirectResponse(f"/devices/{device.id}/firmware-upgrade?plan={plan.id}", status_code=303)


@router.get("/devices/{device_id}/firmware-upgrade", response_class=HTMLResponse, name="firmware_upgrade_plan_page")
def plan_page(request: Request, device_id: uuid.UUID, plan: str | None = None):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "firmware.read"):
            raise HTTPException(403)
        device = _load_device(db, device_id)
        stmt = select(FirmwareUpgradePlan).where(FirmwareUpgradePlan.device_id == device.id)
        if plan:
            try:
                stmt = stmt.where(FirmwareUpgradePlan.id == uuid.UUID(plan))
            except ValueError:
                raise HTTPException(400, "Piano firmware non valido.")
        stmt = stmt.order_by(FirmwareUpgradePlan.created_at.desc()).limit(1)
        upgrade_plan = db.scalar(stmt)
        backup_run = None
        if upgrade_plan:
            sync_plan_status(db, upgrade_plan)
            backup_run = db.get(BackupRun, upgrade_plan.backup_run_id) if upgrade_plan.backup_run_id else None
            db.commit()
        return core.render(
            request,
            db,
            user,
            "firmware_upgrade_plan.html",
            device=device,
            plan=upgrade_plan,
            backup_run=backup_run,
            readiness=_readiness(device),
        )


@router.post("/devices/{device_id}/firmware-upgrade/{plan_id}/approve", name="approve_firmware_upgrade_plan")
def approve_plan(
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
        if not plan or plan.device_id != device.id:
            raise HTTPException(404)
        sync_plan_status(db, plan)
        if plan.status != "ready":
            db.commit()
            raise HTTPException(409, "Il piano non è pronto: backup pre-upgrade non completato con successo.")
        expected = f"UPGRADE {plan.target_version}"
        if confirmation.strip() != expected:
            raise HTTPException(400, f"Conferma non valida. Inserisci esattamente: {expected}")
        plan.status = "approved"
        plan.approved_by = user.id
        plan.approved_at = utcnow()
        core.add_event(
            db,
            "FIRMWARE_UPGRADE_PLAN_APPROVED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={
                "plan_id": str(plan.id),
                "target_version": plan.target_version,
                "backup_run_id": str(plan.backup_run_id),
                "execution_queued": False,
            },
            severity="warning",
            source="portal",
        )
        db.commit()
    return RedirectResponse(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}&approved=1", status_code=303)


@router.post("/devices/{device_id}/firmware-upgrade/{plan_id}/cancel", name="cancel_firmware_upgrade_plan")
def cancel_plan(request: Request, device_id: uuid.UUID, plan_id: uuid.UUID, csrf: str = Form(...)):
    core.validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "firmware.execute")
        device = _load_device(db, device_id)
        plan = db.get(FirmwareUpgradePlan, plan_id)
        if not plan or plan.device_id != device.id:
            raise HTTPException(404)
        if plan.status in {"executing", "success"}:
            raise HTTPException(409, "Il piano non può più essere annullato.")
        plan.status = "cancelled"
        plan.completed_at = utcnow()
        core.add_event(
            db,
            "FIRMWARE_UPGRADE_PLAN_CANCELLED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={"plan_id": str(plan.id), "target_version": plan.target_version},
            source="portal",
        )
        db.commit()
    return RedirectResponse(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}", status_code=303)


def install_firmware_upgrade_planner(app):
    app.include_router(router)
