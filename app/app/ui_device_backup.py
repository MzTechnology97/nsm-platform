"""Device Backup tab for the new interface (phase 1d).

- ``GET    /api/ui/v1/devices/{id}/backups``: capability, effective policy,
  summary, the last 100 runs with their files and restore tests (filters
  ``kind`` all/binary/export and ``status`` all/success/failed);
- ``POST   /api/ui/v1/devices/{id}/backups/run``: immediate agent backup,
  same rules as the classic "Backup adesso" (``backup.execute``);
- ``DELETE /api/ui/v1/devices/{id}/backups/artifacts/{artifact_id}``: removes
  one file from the archive (``backup.configure``), audited like the classic page.

Viewing/diffing exports, downloads and restore tests keep their classic URLs.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app import mikrotik_backup
from app.backup_capabilities import capability_for_device
from app.backup_models import BackupArtifact
from app.backup_storage import remove_artifact_file
from app.db import SessionLocal
from app.device_backup_explorer import _artifact_for_device, _artifact_kind, _device, _manual_backup_context
from app.models import BackupRun, utcnow
from app.restore_test_models import BackupRestoreTest
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
KINDS = ("all", "binary", "export")
STATUSES = ("all", "success", "failed")
RUN_LIMIT = 100


def _iso(value):
    return value.isoformat() if value else None


def block_code(device, policy, credential, pending: bool) -> str | None:
    """Why "Backup adesso" is not available (same order as the classic page)."""
    if device.vendor != "mikrotik":
        return "vendor"
    if device.status != "online":
        return "offline"
    if not credential:
        return "no_agent"
    if not policy:
        return "no_policy"
    if pending:
        return "pending"
    return None


@router.get("/devices/{device_id}/backups")
def device_backups(request: Request, device_id: uuid.UUID, kind: str = "all", status: str = "all"):
    kind = kind if kind in KINDS else "all"
    status = status if status in STATUSES else "all"
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.read")
        device = _device(db, device_id)
        runs = list(db.scalars(select(BackupRun).where(BackupRun.device_id == device.id)
                               .order_by(BackupRun.started_at.desc(), BackupRun.id.desc()).limit(RUN_LIMIT)))
        artifacts = list(db.scalars(select(BackupArtifact).where(BackupArtifact.run_id.in_([r.id for r in runs]), BackupArtifact.deleted_at.is_(None))
                                    .order_by(BackupArtifact.created_at.desc(), BackupArtifact.id.desc()))) if runs else []
        restores: dict[uuid.UUID, BackupRestoreTest] = {}
        if artifacts:
            for test in db.scalars(select(BackupRestoreTest).where(BackupRestoreTest.artifact_id.in_([a.id for a in artifacts]))
                                   .order_by(BackupRestoreTest.performed_at.desc())):
                restores.setdefault(test.artifact_id, test)
        latest_restore = db.scalar(select(BackupRestoreTest).where(BackupRestoreTest.device_id == device.id)
                                   .order_by(BackupRestoreTest.performed_at.desc()).limit(1))
        by_run: dict[uuid.UUID, list[BackupArtifact]] = {}
        for artifact in artifacts:
            by_run.setdefault(artifact.run_id, []).append(artifact)

        base = f"/devices/{device.id}/backups"
        rows = []
        for run in runs:
            if status != "all" and run.status != status:
                continue
            files = [a for a in by_run.get(run.id, []) if kind == "all" or _artifact_kind(a) == kind]
            if kind != "all" and not files:
                continue
            rows.append({
                "id": str(run.id), "status": run.status, "backup_type": run.backup_type, "error": run.error_message,
                "started_at": _iso(run.started_at), "completed_at": _iso(run.completed_at),
                "files": [{
                    "id": str(a.id), "filename": a.filename, "size": a.size_bytes, "sha256": a.sha256, "kind": _artifact_kind(a),
                    "restore": {"result": restores[a.id].result, "performed_at": _iso(restores[a.id].performed_at)} if a.id in restores else None,
                    "view_url": f"{base}/artifacts/{a.id}/view" if _artifact_kind(a) == "export" else None,
                    "download_url": f"{base}/artifacts/{a.id}/download",
                    "restore_url": f"{base}/artifacts/{a.id}/restore-test",
                } for a in files],
            })

        policy, credential, pending = _manual_backup_context(db, device)
        capability = capability_for_device(db, device)
        latest = next((r for r in runs if r.status == "success"), None)
        can_execute = core.has_permission(user, "backup.execute")
        code = block_code(device, policy, credential, pending)
        return {
            "capability": {"method": capability.label, "status": capability.status, "executable": capability.executable, "reason": capability.reason},
            "policy": {"id": str(policy.id), "name": policy.name, "schedule": policy.schedule_cron} if policy else None,
            "summary": {
                "latest_success": _iso(latest.completed_at or latest.started_at) if latest else None,
                "latest_type": latest.backup_type if latest else None,
                "files": len(artifacts),
                "readable": sum(1 for a in artifacts if _artifact_kind(a) == "export"),
                "bytes": sum(int(a.size_bytes or 0) for a in artifacts),
                "restore": {"result": latest_restore.result, "performed_at": _iso(latest_restore.performed_at)} if latest_restore else None,
            },
            "run": {"pending": pending, "blocker": code, "can_execute": can_execute, "can_run": bool(can_execute and code is None)},
            "can_delete": core.has_permission(user, "backup.configure"),
            "runs": rows,
            "links": {"classic": base, "restore_tests": f"{base}/restore-tests", "policies": "/backups"},
        }


@router.post("/devices/{device_id}/backups/run", dependencies=[Depends(require_csrf)])
def device_backup_run(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.execute")
        device = _device(db, device_id)
        policy, credential, pending = _manual_backup_context(db, device)
        code = block_code(device, policy, credential, pending)
        if code == "pending":
            return {"queued": False, "already": True}
        if code:
            raise HTTPException(409, {"code": code, "message": "Backup non avviato."})
        try:
            run = mikrotik_backup.enqueue_mikrotik_backup(db, device, user)
        except HTTPException as exc:
            raise HTTPException(exc.status_code, {"code": "refused", "message": str(exc.detail)}) from exc
        if run is None:
            return {"queued": False, "already": True}
        db.commit()
        return {"queued": True, "already": False, "run_id": str(run.id)}


@router.delete("/devices/{device_id}/backups/artifacts/{artifact_id}", dependencies=[Depends(require_csrf)])
def device_backup_delete(request: Request, device_id: uuid.UUID, artifact_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.configure")
        device = _device(db, device_id)
        artifact, run = _artifact_for_device(db, device.id, artifact_id)
        removed = remove_artifact_file(artifact.storage_path)
        artifact.deleted_at = utcnow()
        artifact.deleted_by_user_id = user.id
        core.add_event(db, "BACKUP_ARTIFACT_DELETED", actor=user, customer_id=device.customer_id, device_id=device.id,
                       details={"artifact_id": str(artifact.id), "run_id": str(run.id), "filename": artifact.filename,
                                "file_removed": removed, "scope": "ui_device_backup"},
                       severity="warning", source="portal")
        db.commit()
        return {"deleted": True, "file_removed": removed}


def install_ui_device_backup(app) -> None:
    app.include_router(router)
