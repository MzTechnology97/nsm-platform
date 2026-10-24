"""Device Backup tab for the new interface (phase 1d).

- ``GET    /api/ui/v1/devices/{id}/backups``: capability, effective policy,
  summary, the last 100 runs with their files and restore tests (filters
  ``kind`` all/binary/export and ``status`` all/success/failed);
- ``POST   /api/ui/v1/devices/{id}/backups/run``: immediate agent backup,
  same rules as the classic "Backup adesso" (``backup.execute``);
- ``DELETE /api/ui/v1/devices/{id}/backups/artifacts/{artifact_id}``: removes
  one file from the archive (``backup.configure``), audited like the classic page;
- ``GET    /api/ui/v1/devices/{id}/backups/artifacts/{artifact_id}``: a text export
  with its line count, optionally diffed with ``against`` (an older export of the
  same device), audited like the classic viewer (``backup.read``);
- ``GET    /api/ui/v1/devices/{id}/backups/restore-tests``: restore-test evidence of
  the device (``backup.read``);
- ``GET/POST /api/ui/v1/devices/{id}/backups/artifacts/{artifact_id}/restore-test``:
  integrity re-check and history of one file, and recording a new outcome
  (``backup.execute``, same checks as the classic form).

Downloads keep their classic URL.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app import main as core
from app import mikrotik_backup
from app.backup_capabilities import capability_for_device
from app import backup_restore_tests as restore_tests
from app.backup_models import BackupArtifact
from app.backup_storage import remove_artifact_file
from app.db import SessionLocal
from app.device_backup_explorer import _artifact_for_device, _artifact_kind, _device, _manual_backup_context, artifact_text_record
from app.input_errors import InputError
from app.models import BackupRun, User, utcnow
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
                    "viewable": _artifact_kind(a) == "export",
                    "download_url": f"{base}/artifacts/{a.id}/download",
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
            "customer_id": str(device.customer_id),
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


READ_ERRORS = {413: "too_large", 415: "binary"}


def _file(artifact, run) -> dict:
    return {
        "id": str(artifact.id), "filename": artifact.filename, "size": artifact.size_bytes, "sha256": artifact.sha256,
        "created_at": _iso(artifact.created_at), "run_started_at": _iso(run.started_at), "backup_type": run.backup_type,
    }


@router.get("/devices/{device_id}/backups/artifacts/{artifact_id}")
def device_backup_artifact(request: Request, device_id: uuid.UUID, artifact_id: uuid.UUID, against: str = ""):
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.read")
        device = _device(db, device_id)
        try:
            view = artifact_text_record(db, user, device, artifact_id, against)
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc
        except HTTPException as exc:
            if exc.status_code in READ_ERRORS:
                raise HTTPException(422, {"code": READ_ERRORS[exc.status_code], "message": str(exc.detail)}) from exc
            raise
        artifact, base = view["artifact"], f"/devices/{device.id}/backups/artifacts"
        diff = None
        if view["against_artifact"] is not None:
            diff = {"rows": view["diff_rows"], "summary": view["diff_summary"]}
        return {
            "artifact": _file(artifact, view["run"]),
            "artifact_type": artifact.artifact_type,
            "lines": view["text"].splitlines(),
            "candidates": [_file(a, r) for a, r in view["candidates"]],
            "against": _file(view["against_artifact"], view["against_run"]) if view["against_artifact"] is not None else None,
            "diff": diff,
            "links": {"download": f"{base}/{artifact.id}/download"},
        }


def _restore_test(test: BackupRestoreTest, users: dict, live: set) -> dict:
    return {
        "id": str(test.id), "result": test.result, "method": test.method, "target": test.target_label,
        "target_version": test.target_routeros_version, "source_version": test.source_routeros_version, "notes": test.notes,
        "performed_at": _iso(test.performed_at), "recorded_at": _iso(test.recorded_at),
        "performed_by": users.get(test.performed_by_user_id),
        "integrity_ok": bool((test.integrity or {}).get("ok")),
        "artifact": {"id": str(test.artifact_id) if test.artifact_id else None, "filename": test.artifact_filename, "type": test.artifact_type,
                     "sha256": test.artifact_sha256, "size": test.artifact_size_bytes, "live": test.artifact_id in live},
    }


def _restore_rows(db, tests: list[BackupRestoreTest], live: set) -> list[dict]:
    user_ids = {t.performed_by_user_id for t in tests if t.performed_by_user_id}
    users = {u.id: u.username for u in db.scalars(select(User).where(User.id.in_(user_ids)))} if user_ids else {}
    return [_restore_test(t, users, live) for t in tests]


def _live_artifacts(db, device_id) -> set:
    return set(db.scalars(select(BackupArtifact.id).join(BackupRun, BackupRun.id == BackupArtifact.run_id)
                          .where(BackupRun.device_id == device_id, BackupArtifact.deleted_at.is_(None))))


@router.get("/devices/{device_id}/backups/restore-tests")
def device_restore_tests(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        ui_user(request, db, "backup.read")
        device = _device(db, device_id)
        tests = list(db.scalars(select(BackupRestoreTest).where(BackupRestoreTest.device_id == device.id)
                                .order_by(BackupRestoreTest.performed_at.desc()).limit(200)))
        return {"tests": _restore_rows(db, tests, _live_artifacts(db, device.id))}


@router.get("/devices/{device_id}/backups/artifacts/{artifact_id}/restore-test")
def device_restore_test_form(request: Request, device_id: uuid.UUID, artifact_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.read")
        device = _device(db, device_id)
        artifact, run = _artifact_for_device(db, device.id, artifact_id)
        history = list(db.scalars(select(BackupRestoreTest).where(BackupRestoreTest.artifact_id == artifact.id)
                                  .order_by(BackupRestoreTest.performed_at.desc())))
        integrity = restore_tests.evaluate_artifact_integrity(artifact)
        return {
            "artifact": {**_file(artifact, run), "type": artifact.artifact_type, "kind": _artifact_kind(artifact), "run_status": run.status},
            "integrity": {key: integrity.get(key) for key in ("ok", "file_present", "size_matches", "sha256_matches", "checked_at")},
            "source_version": restore_tests.source_routeros_version(artifact),
            "methods": list(restore_tests.METHODS),
            "results": list(restore_tests.RESULTS),
            "history": _restore_rows(db, history, {artifact.id}),
            "can_record": core.has_permission(user, "backup.execute"),
            "links": {"download": f"/devices/{device.id}/backups/artifacts/{artifact.id}/download"},
        }


class RestoreTestBody(BaseModel):
    method: str = ""
    target_label: str = ""
    target_routeros_version: str = ""
    result: str = ""
    performed_at: str = ""
    notes: str = ""


@router.post("/devices/{device_id}/backups/artifacts/{artifact_id}/restore-test", dependencies=[Depends(require_csrf)])
def device_restore_test_record(request: Request, device_id: uuid.UUID, artifact_id: uuid.UUID, body: RestoreTestBody):
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.execute")
        device = _device(db, device_id)
        try:
            test = restore_tests.record_restore_test(db, user, device, artifact_id, **body.model_dump())
        except InputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc
        return {"id": str(test.id), "result": test.result}
