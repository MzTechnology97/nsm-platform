"""MikroTik fleet worklists rebuilt from ideas of the classic interface.

* **Restore readiness** (Core 0.50 *restore readiness* idea, on top of MTK-03):
  for every MikroTik, whether NSM holds a recent and complete backup (every
  artifact format the installed agent can produce) and a recent *passed*
  restore test recorded against an intact artifact.  Read-only: it never
  restores anything, it only points to the backup and restore-test pages.
* **RouterBOOT fleet** (Core 0.39 *routerboot fleet* idea): MikroTiks whose
  RouterBOOT differs from the firmware it can be upgraded to, with the state of
  the per-device RouterBOOT workflow and why it cannot run yet.  No bulk action:
  the upgrade stays the confirmed per-device flash + reboot
  (``routerboot_lifecycle``).

Endpoints (new interface):

- ``GET /api/ui/v1/backups/restore-readiness`` (``backup.read``);
- ``GET /api/ui/v1/firmware/routerboot`` (``firmware.read``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.backup_models import BackupArtifact
from app.db import SessionLocal
from app.models import BackupRun, Customer, Device, utcnow
from app.restore_test_models import BackupRestoreTest
from app.ui_api import brand_icon, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
BACKUP_MAX_AGE = timedelta(days=7)
RESTORE_TEST_MAX_AGE = timedelta(days=180)
READINESS_STALE_AFTER = timedelta(hours=24)
MAX_ROWS = 500
RESTORE_STATES = ("no_backup", "backup_stale", "backup_incomplete", "restore_failed", "no_restore_test", "restore_stale", "ready")
ROUTERBOOT_STATES = ("pending", "blocked", "in_progress", "failed", "unknown", "current")
ROUTERBOOT_ACTIVE = {"staging", "staged", "reboot_pending", "rebooting"}


def _aware(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value or None


def _uuid(value):
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def _device_base(device: Device) -> dict:
    return {
        "id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device),
        "customer": {"id": str(device.customer.id), "name": device.customer.name} if device.customer else None,
        "site": device.site.name if device.site else None, "model": device.model, "status": device.status,
        "firmware": device.firmware_version,
    }


def _mikrotiks(db, customer_id, q: str) -> list[Device]:
    stmt = select(Device).where(Device.vendor == "mikrotik").options(selectinload(Device.customer), selectinload(Device.site))
    if customer_id:
        stmt = stmt.where(Device.customer_id == customer_id)
    term = q.strip()
    if term:
        like = f"%{term}%"
        stmt = stmt.outerjoin(Customer, Customer.id == Device.customer_id).where(or_(
            Device.display_name.ilike(like), Device.name.ilike(like), Device.device_identity.ilike(like), Device.model.ilike(like),
            Device.serial_number.ilike(like), Device.management_ip.ilike(like), Customer.name.ilike(like)))
    return list(db.scalars(stmt.order_by(Device.customer_id, Device.display_name.nullslast(), Device.name)).unique())


def _filtered(rows: list[dict], state: str, states: tuple, good: str) -> tuple[dict, list[dict]]:
    counts = {key: sum(1 for row in rows if row["state"] == key) for key in states}
    counts["attention"] = len(rows) - counts[good]
    counts["all"] = len(rows)
    if state == "attention":
        rows = [row for row in rows if row["state"] != good]
    elif state in states:
        rows = [row for row in rows if row["state"] == state]
    return counts, rows


# --- restore readiness ----------------------------------------------------------------------------

def expected_artifacts(db, device) -> tuple[str, ...]:
    """Artifact formats the installed agent can produce (``backup_capabilities``)."""
    from app.backup_capabilities import capability_for_device

    try:
        return tuple(capability_for_device(db, device).artifact_types or ())
    except Exception:  # noqa: BLE001 - a capability error must not hide the device from the worklist
        return ("mikrotik_binary", "mikrotik_export")


def restore_row(device: Device, run: BackupRun | None, artifact_types: set[str], expected: tuple[str, ...],
                test: BackupRestoreTest | None, now: datetime) -> dict:
    """One worklist row; ``state`` is the first missing step of: backup, fresh backup, complete backup, restore test."""
    completed = _aware(run.completed_at or run.started_at) if run else None
    missing = [kind for kind in expected if kind not in artifact_types]
    performed = _aware(test.performed_at) if test else None
    integrity_ok = bool(test and (test.integrity or {}).get("ok"))
    if run is None:
        state = "no_backup"
    elif completed is None or now - completed > BACKUP_MAX_AGE:
        state = "backup_stale"
    elif missing:
        state = "backup_incomplete"
    elif test is not None and (test.result != "passed" or not integrity_ok):
        state = "restore_failed"
    elif test is None:
        state = "no_restore_test"
    elif performed is None or now - performed > RESTORE_TEST_MAX_AGE:
        state = "restore_stale"
    else:
        state = "ready"
    return {
        **_device_base(device),
        "state": state,
        "backup": {"id": str(run.id), "completed_at": _iso(completed), "formats": sorted(artifact_types), "missing": missing} if run else None,
        "restore_test": {"id": str(test.id), "result": test.result, "performed_at": _iso(performed), "integrity_ok": integrity_ok,
                         "method": test.method} if test else None,
    }


def restore_readiness(db, customer_id=None, q: str = "", now=None) -> list[dict]:
    now = _aware(now) or utcnow()
    devices = _mikrotiks(db, customer_id, q)
    ids = [device.id for device in devices]
    if not ids:
        return []
    latest_run: dict = {}
    for run in db.scalars(select(BackupRun).where(BackupRun.device_id.in_(ids), BackupRun.status == "success")
                          .order_by(BackupRun.completed_at.desc().nullslast(), BackupRun.started_at.desc())):
        latest_run.setdefault(run.device_id, run)
    formats: dict = {}
    run_ids = [run.id for run in latest_run.values()]
    if run_ids:
        for run_id, kind in db.execute(select(BackupArtifact.run_id, BackupArtifact.artifact_type)
                                       .where(BackupArtifact.run_id.in_(run_ids), BackupArtifact.deleted_at.is_(None))):
            formats.setdefault(run_id, set()).add(kind)
    latest_test: dict = {}
    for test in db.scalars(select(BackupRestoreTest).where(BackupRestoreTest.device_id.in_(ids))
                           .order_by(BackupRestoreTest.performed_at.desc(), BackupRestoreTest.recorded_at.desc())):
        latest_test.setdefault(test.device_id, test)
    rows = []
    for device in devices:
        run = latest_run.get(device.id)
        rows.append(restore_row(device, run, formats.get(run.id, set()) if run else set(), expected_artifacts(db, device),
                                latest_test.get(device.id), now))
    return rows


# --- RouterBOOT fleet -----------------------------------------------------------------------------

def routerboot_row(device: Device, now: datetime) -> dict:
    from app import routerboot_lifecycle as rb

    data = dict(device.inventory_data or {})
    readiness = dict(data.get("firmware_readiness") or {})
    lifecycle = dict(data.get(rb.STATE_KEY) or {})
    current = str(readiness.get("routerboard_current") or device.routerboot_version or "").strip()
    available = str(readiness.get("routerboard_upgrade") or "").strip()
    workflow = str(lifecycle.get("status") or "idle").strip().lower()
    transport = str(data.get("agent_transport") or "").strip().lower()
    profile = str(data.get("agent_privilege_profile") or "").strip()
    checked = _aware(readiness.get("checked_at"))
    reason = None
    if workflow in ROUTERBOOT_ACTIVE:
        state = "in_progress"
    elif current and available and current == available:
        state = "current"
    elif workflow == "failed":
        state = "failed"
    elif current and available:
        # Same gates as routerboot_lifecycle._validate_modern_executor, shown before the operator opens the device.
        if transport != "modern":
            state, reason = "blocked", "not_modern"
        elif profile not in rb.OPERATIONAL_PROFILES:
            state, reason = "blocked", "profile"
        elif device.status != "online":
            state, reason = "blocked", "offline"
        else:
            state = "pending"
    else:
        state, reason = "unknown", "not_read"
    return {
        **_device_base(device),
        "state": state, "reason": reason, "workflow": workflow,
        "current": current or None, "available": available or None, "target": lifecycle.get("target_version"),
        "last_error": lifecycle.get("last_error"), "checked_at": _iso(checked),
        "stale": checked is None or now - checked > READINESS_STALE_AFTER,
    }


def routerboot_fleet(db, customer_id=None, q: str = "", now=None) -> list[dict]:
    now = _aware(now) or utcnow()
    return [routerboot_row(device, now) for device in _mikrotiks(db, customer_id, q)]


# --- endpoints ------------------------------------------------------------------------------------

@router.get("/backups/restore-readiness")
def restore_readiness_api(request: Request, state: str = "attention", customer: str = "", q: str = ""):
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.read")
        state = state if state in RESTORE_STATES + ("attention", "all") else "attention"
        counts, rows = _filtered(restore_readiness(db, _uuid(customer), q[:100]), state, RESTORE_STATES, "ready")
        return {"state": state, "counts": counts, "total": len(rows), "items": rows[:MAX_ROWS], "limit": MAX_ROWS,
                "backup_max_age_days": BACKUP_MAX_AGE.days, "restore_test_max_age_days": RESTORE_TEST_MAX_AGE.days,
                "can_record": core.has_permission(user, "backup.execute")}


@router.get("/firmware/routerboot")
def routerboot_fleet_api(request: Request, state: str = "attention", customer: str = "", q: str = ""):
    with SessionLocal() as db:
        user = ui_user(request, db, "firmware.read")
        state = state if state in ROUTERBOOT_STATES + ("attention", "all") else "attention"
        counts, rows = _filtered(routerboot_fleet(db, _uuid(customer), q[:100]), state, ROUTERBOOT_STATES, "current")
        return {"state": state, "counts": counts, "total": len(rows), "items": rows[:MAX_ROWS], "limit": MAX_ROWS,
                "can_execute": core.has_permission(user, "firmware.execute")}


def install_mikrotik_fleet_readiness(app) -> None:
    app.include_router(router)
