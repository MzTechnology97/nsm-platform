"""Scheduled RouterOS upgrades (FW-SCHED, operator request 2026-10-23).

An operator schedules the upgrade of one MikroTik (device *Firmware* tab) or of several (firmware worklist) at a time,
for example at night. Scheduling is the only confirmation: at that time the worker runs every phase by itself, with the
scheduling operator as actor and every step audited by the phase it runs:

1. readiness check (when the last one is older than 6 hours) and the upgrade plan, with a new pre-upgrade backup;
2. approval, package download and activation with reboot, each as soon as the previous one has finished;
3. the plan's own verification of the installed version after the reboot (``firmware_activation``).

An existing plan is reused only when it is still before the download and its backup is less than 12 hours old;
otherwise it is cancelled and a new one is made. A device that stays offline, or does not start, within the window
(2 hours from the scheduled time) is marked *non eseguito*; a plan that fails stops the schedule with its reason.
The schedule is kept on the device (``inventory_data.firmware_schedule``), so no migration is needed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import firmware_upgrade_planner as planner
from app import main as core
from app.db import SessionLocal
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.input_errors import InputError
from app.models import BackupRun, Device, User, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
KEY = "firmware_schedule"
START_WINDOW = timedelta(hours=2)
MAX_AHEAD = timedelta(days=30)
BACKUP_MAX_AGE = timedelta(hours=12)
ACTIVE = ("scheduled", "checking", "running")
DONE = {"success"}
ENDED = {"success", "failed", "cancelled"}
BEFORE_DOWNLOAD = {"backup_pending", "ready", "approved"}


class ScheduleError(InputError, ValueError):
    pass


def schedule_of(device: Device) -> dict | None:
    value = (device.inventory_data or {}).get(KEY)
    return dict(value) if isinstance(value, dict) else None


def _save(device: Device, value: dict | None) -> None:
    data = dict(device.inventory_data or {})
    if value is None:
        data.pop(KEY, None)
    else:
        data[KEY] = value
    device.inventory_data = data


def _parse(value) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else None


def _eligible(device: Device) -> str | None:
    """Why the device cannot get a scheduled RouterOS upgrade, else None."""
    if device.vendor != "mikrotik":
        return "vendor"
    if not planner._modern_routeros(device):
        return "agent"
    return None


def schedule_device(db, user, device: Device, at_value) -> dict:
    """Schedule (or move) the upgrade of one MikroTik at ``at_value`` (ISO time with offset); audited. The caller commits."""
    now = utcnow()
    at = _parse(at_value)
    if at is None:
        raise ScheduleError("time", "Indica data e ora dell'aggiornamento.")
    if at < now + timedelta(minutes=1) or at > now + MAX_AHEAD:
        raise ScheduleError("time", "L'aggiornamento si programma da un minuto a 30 giorni da adesso.")
    reason = _eligible(device)
    if reason:
        raise ScheduleError(reason, "Aggiornamento programmato disponibile solo su MikroTik con RouterOS 7.13+ e agent moderno.")
    current = schedule_of(device)
    if current and current.get("state") == "running":
        raise ScheduleError("running", "L'aggiornamento programmato è già in corso su questo apparato.")
    value = {"at": at.isoformat(), "user_id": str(user.id) if user else None, "by": user.username if user else None,
             "created_at": now.isoformat(), "state": "scheduled", "plan_id": None, "error": None, "target_version": None}
    _save(device, value)
    core.add_event(db, "FIRMWARE_UPGRADE_SCHEDULED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"at": value["at"], "moved": bool(current and current.get("state") in ACTIVE)})
    return value


def cancel_schedule(db, user, device: Device) -> None:
    """Remove a schedule that has not started the download yet; a plan it already opened is cancelled too. The caller commits."""
    current = schedule_of(device)
    if not current or current.get("state") not in ACTIVE:
        raise ScheduleError("none", "Nessun aggiornamento programmato da annullare.")
    plan = db.get(FirmwareUpgradePlan, uuid.UUID(current["plan_id"])) if current.get("plan_id") else None
    if plan and plan.status not in ENDED:
        if plan.status not in BEFORE_DOWNLOAD:
            raise ScheduleError("running", "Il download è già avviato: annulla il piano dalla scheda Firmware se è ancora possibile.")
        planner.cancel_record(db, user, device, plan.id)
    _save(device, {**current, "state": "cancelled", "ended_at": utcnow().isoformat()})
    core.add_event(db, "FIRMWARE_UPGRADE_SCHEDULE_CANCELLED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"at": current.get("at")})


def _finish(db, device, value: dict, state: str, error: str | None = None) -> None:
    value = {**value, "state": state, "error": error, "ended_at": utcnow().isoformat()}
    _save(device, value)
    core.add_event(db, "FIRMWARE_UPGRADE_SCHEDULE_DONE" if state == "success" else "FIRMWARE_UPGRADE_SCHEDULE_FAILED",
                   customer_id=device.customer_id, device_id=device.id, source="worker",
                   severity="info" if state == "success" else "warning", result="success" if state == "success" else "failed",
                   details={"at": value.get("at"), "plan_id": value.get("plan_id"), "target_version": value.get("target_version"), "error": error})


def _reusable(db, plan: FirmwareUpgradePlan | None, now: datetime) -> bool:
    if plan is None or plan.status not in BEFORE_DOWNLOAD:
        return False
    run = db.get(BackupRun, plan.backup_run_id) if plan.backup_run_id else None
    started = run.started_at if run else None
    return started is not None and now - started <= BACKUP_MAX_AGE


def _start(db, device, value: dict, user, now: datetime) -> dict:
    """At the scheduled time: reuse a recent plan, else open one (checking first when the readiness is old)."""
    plan = db.scalar(select(FirmwareUpgradePlan).where(FirmwareUpgradePlan.device_id == device.id)
                     .order_by(FirmwareUpgradePlan.created_at.desc()).limit(1))
    if plan is not None and plan.status not in ENDED:
        planner.sync_plan_status(db, plan)
        if not _reusable(db, plan, now):
            if plan.status not in BEFORE_DOWNLOAD:
                raise HTTPException(409, "C'è già un aggiornamento in corso su questo apparato.")
            planner.cancel_record(db, user, device, plan.id)
            plan = None
    if plan is not None and plan.status not in ENDED:
        return {**value, "state": "running", "plan_id": str(plan.id), "target_version": plan.target_version, "started_at": now.isoformat()}
    result = planner.request_plan(db, device, user)
    if result.get("checking"):
        return {**value, "state": "checking", "started_at": now.isoformat()}
    plan = db.get(FirmwareUpgradePlan, uuid.UUID(result["plan_id"]))
    return {**value, "state": "running", "plan_id": result["plan_id"], "target_version": plan.target_version, "started_at": now.isoformat()}


def _advance(db, device, value: dict, user) -> dict | None:
    """One step of a running schedule; returns the new value, or None when it ended (``_finish`` already saved it)."""
    from app import firmware_activation, firmware_package_staging

    plan = db.get(FirmwareUpgradePlan, uuid.UUID(value["plan_id"]))
    if plan is None:
        _finish(db, device, value, "failed", "Piano firmware non trovato.")
        return None
    planner.sync_plan_status(db, plan)
    target = plan.target_version
    if plan.status == "success":
        _finish(db, device, value, "success")
        return None
    if plan.status in {"failed", "cancelled"}:
        _finish(db, device, value, "failed", plan.last_error or ("Piano annullato." if plan.status == "cancelled" else "Piano non riuscito."))
        return None
    if device.status != "online":
        return value  # the next step waits for the router (a reboot or a short outage)
    if plan.status == "ready":
        planner.approve_record(db, user, device, plan.id, f"UPGRADE {target}")
    elif plan.status == "approved":
        firmware_package_staging.stage_record(db, user, device, plan.id, f"DOWNLOAD {target}")
    elif plan.status == "staged":
        firmware_activation.activate_record(db, user, device, plan.id, f"ACTIVATE {target}")
    return {**value, "plan_status": plan.status}


def tick(now: datetime | None = None) -> dict:
    """Worker, every minute: start the schedules that are due and move the running ones one phase forward."""
    now = now or utcnow()
    stats = {"started": 0, "advanced": 0, "done": 0, "failed": 0}
    with SessionLocal() as db:
        devices = [d for d in db.scalars(select(Device).where(Device.vendor == "mikrotik"))
                   if (schedule_of(d) or {}).get("state") in ACTIVE]
        for device in devices:
            value = schedule_of(device)
            at = _parse(value.get("at"))
            user = db.get(User, uuid.UUID(value["user_id"])) if value.get("user_id") else None
            try:
                if value["state"] == "scheduled":
                    if at is None or at > now:
                        continue
                    if now - at > START_WINDOW:
                        _finish(db, device, value, "failed", "Non eseguito: l'apparato non era raggiungibile nella finestra di 2 ore.")
                        stats["failed"] += 1
                    elif device.status == "online":
                        value = _start(db, device, value, user, now)
                        _save(device, value)
                        core.add_event(db, "FIRMWARE_UPGRADE_SCHEDULE_STARTED", actor=user, customer_id=device.customer_id, device_id=device.id,
                                       source="worker", details={"at": value.get("at"), "plan_id": value.get("plan_id"), "state": value["state"]})
                        stats["started"] += 1
                elif value["state"] == "checking":
                    request = planner.prepare_state(device)
                    if request and request.get("error"):
                        planner_data = dict(device.inventory_data or {})
                        planner_data.pop(planner.PREPARE_KEY, None)
                        device.inventory_data = planner_data
                        _finish(db, device, value, "failed", request["error"])
                        stats["failed"] += 1
                        continue
                    plan = db.scalar(select(FirmwareUpgradePlan).where(FirmwareUpgradePlan.device_id == device.id)
                                     .order_by(FirmwareUpgradePlan.created_at.desc()).limit(1))
                    started = _parse(value.get("started_at"))
                    if plan is not None and started is not None and plan.created_at >= started - timedelta(seconds=5) and plan.status not in ENDED:
                        _save(device, {**value, "state": "running", "plan_id": str(plan.id), "target_version": plan.target_version})
                        stats["advanced"] += 1
                    elif request is None and started is not None and now - started > timedelta(minutes=30):
                        _finish(db, device, value, "failed", "La verifica degli aggiornamenti non è arrivata.")
                        stats["failed"] += 1
                else:
                    advanced = _advance(db, device, value, user)
                    if advanced is None:
                        stats["done" if (schedule_of(device) or {}).get("state") == "success" else "failed"] += 1
                    else:
                        _save(device, advanced)
                        stats["advanced"] += 1
            except HTTPException as exc:
                db.rollback()
                device = db.get(Device, device.id)
                _finish(db, device, schedule_of(device) or value, "failed", str(exc.detail)[:300])
                stats["failed"] += 1
            db.commit()
    return stats


# ------------------------------------------------------------------- API --

def _fail(exc: InputError):
    raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc


def _mikrotik(db, device_id) -> Device:
    device = db.get(Device, device_id)
    if device is None or device.vendor != "mikrotik":
        raise HTTPException(404)
    return device


@router.post("/devices/{device_id}/firmware-upgrade/schedule", dependencies=[Depends(require_csrf)])
def device_schedule(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        device = _mikrotik(db, device_id)
        try:
            value = schedule_device(db, user, device, body.get("at"))
        except InputError as exc:
            _fail(exc)
        db.commit()
        return {"schedule": value}


@router.post("/devices/{device_id}/firmware-upgrade/schedule/cancel", dependencies=[Depends(require_csrf)])
def device_schedule_cancel(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        device = _mikrotik(db, device_id)
        try:
            cancel_schedule(db, user, device)
        except InputError as exc:
            _fail(exc)
        db.commit()
        return {"cancelled": True}


@router.post("/firmware/schedule", dependencies=[Depends(require_csrf)])
def bulk_schedule(request: Request, body: dict = Body(default={})):
    """Schedule several MikroTik at the same time (firmware worklist); devices that cannot be scheduled are listed."""
    ids = body.get("device_ids") if isinstance(body.get("device_ids"), list) else []
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.execute")
        scheduled, skipped = [], []
        for raw in ids[:500]:
            try:
                device = db.get(Device, uuid.UUID(str(raw)))
            except ValueError:
                device = None
            if device is None:
                continue
            try:
                schedule_device(db, user, device, body.get("at"))
                scheduled.append(str(device.id))
            except InputError as exc:
                if exc.code == "time":
                    _fail(exc)
                skipped.append({"id": str(device.id), "name": device.display_name or device.name, "reason": exc.code})
        db.commit()
        return {"scheduled": len(scheduled), "skipped": skipped}


def api_state(device: Device) -> dict | None:
    value = schedule_of(device)
    if not value:
        return None
    return {key: value.get(key) for key in ("at", "by", "state", "plan_id", "target_version", "error", "started_at", "ended_at")}


def install_firmware_schedule(app) -> None:
    app.include_router(router)
