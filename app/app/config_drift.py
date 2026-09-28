"""Core 0.44 MikroTik configuration history and drift detection."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app import main as core
from app.backup_models import BackupArtifact
from app.backup_text_tools import _diff_rows, _read_export
from app.db import SessionLocal
from app.models import ActionIssue, BackupRun, Device, utcnow
from app.security import validate_csrf

router = APIRouter()
BASELINE_KEY = "config_baseline_artifact_id"
BASELINE_AT_KEY = "config_baseline_at"
DRIFT_CATEGORY = "configuration"
DRIFT_TITLE = "Configurazione MikroTik modificata"


def _exports(db, device_id: uuid.UUID, limit: int = 50):
    return list(db.execute(
        select(BackupArtifact, BackupRun)
        .join(BackupRun, BackupRun.id == BackupArtifact.run_id)
        .where(
            BackupRun.device_id == device_id,
            BackupRun.status == "success",
            BackupArtifact.artifact_type == "mikrotik_export",
            BackupArtifact.deleted_at.is_(None),
        )
        .order_by(BackupRun.completed_at.desc(), BackupArtifact.created_at.desc())
        .limit(limit)
    ).all())


def _artifact_for_device(db, device_id: uuid.UUID, artifact_id: uuid.UUID):
    return db.execute(
        select(BackupArtifact, BackupRun)
        .join(BackupRun, BackupRun.id == BackupArtifact.run_id)
        .where(
            BackupArtifact.id == artifact_id,
            BackupRun.device_id == device_id,
            BackupArtifact.artifact_type == "mikrotik_export",
            BackupArtifact.deleted_at.is_(None),
        )
    ).first()


def _baseline_id(device: Device):
    raw = (device.inventory_data or {}).get(BASELINE_KEY)
    try:
        return uuid.UUID(str(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def _open_drift_issue(db, device_id: uuid.UUID):
    return db.scalar(select(ActionIssue).where(
        ActionIssue.device_id == device_id,
        ActionIssue.category == DRIFT_CATEGORY,
        ActionIssue.title == DRIFT_TITLE,
        ActionIssue.status.in_(["open", "acknowledged"]),
    ))


def _normalized_export(text: str):
    lines = []
    for raw in text.splitlines():
        line = raw.rstrip()
        lower = line.lower().strip()
        if lower.startswith("# ") and (" by routeros " in lower or "software id" in lower or lower.startswith("# 20")):
            continue
        lines.append(line)
    return "\n".join(lines).strip() + "\n"


def _compare_artifacts(before: BackupArtifact, after: BackupArtifact):
    _rows, summary = _diff_rows(_normalized_export(_read_export(before)), _normalized_export(_read_export(after)))
    return summary


def evaluate_config_drift(db, device: Device, new_artifact: BackupArtifact):
    baseline_id = _baseline_id(device)
    reference = None
    reference_kind = "previous"
    if baseline_id and baseline_id != new_artifact.id:
        row = _artifact_for_device(db, device.id, baseline_id)
        if row:
            reference = row[0]
            reference_kind = "baseline"
    if reference is None:
        for artifact, _run in _exports(db, device.id, limit=3):
            if artifact.id != new_artifact.id:
                reference = artifact
                break
    if reference is None:
        core.add_event(db, "CONFIG_BASELINE_CANDIDATE_CREATED", customer_id=device.customer_id, device_id=device.id, details={"artifact_id": str(new_artifact.id)}, source="backup_engine")
        return {"state": "first_export", "changed": False}
    try:
        summary = _compare_artifacts(reference, new_artifact)
    except HTTPException as exc:
        core.add_event(db, "CONFIG_DRIFT_CHECK_FAILED", customer_id=device.customer_id, device_id=device.id, severity="warning", result="failed", details={"artifact_id": str(new_artifact.id), "reason": exc.detail}, source="backup_engine")
        return {"state": "error", "changed": False}
    changed = bool(summary["added"] or summary["removed"])
    issue = _open_drift_issue(db, device.id)
    if changed:
        details = {"reference_artifact_id": str(reference.id), "artifact_id": str(new_artifact.id), "reference_kind": reference_kind, "added": summary["added"], "removed": summary["removed"], "detected_at": utcnow().isoformat()}
        if issue:
            issue.updated_at = utcnow(); issue.details = details; issue.severity = "warning"
        else:
            db.add(ActionIssue(category=DRIFT_CATEGORY, severity="warning", status="open", title=DRIFT_TITLE, details=details, customer_id=device.customer_id, device_id=device.id))
        core.add_event(db, "CONFIG_DRIFT_DETECTED", customer_id=device.customer_id, device_id=device.id, severity="warning", details=details, source="backup_engine")
    else:
        if issue:
            issue.status = "resolved"; issue.resolved_at = utcnow(); issue.updated_at = utcnow()
        core.add_event(db, "CONFIG_DRIFT_CHECKED", customer_id=device.customer_id, device_id=device.id, details={"reference_artifact_id": str(reference.id), "artifact_id": str(new_artifact.id), "reference_kind": reference_kind, "changed": False}, source="backup_engine")
    return {"state": "changed" if changed else "unchanged", "changed": changed, **summary}


def _history_rows(db, device: Device):
    exports = _exports(db, device.id, limit=50)
    rows = []
    for index, (artifact, run) in enumerate(exports):
        previous = exports[index + 1][0] if index + 1 < len(exports) else None
        summary = None
        if previous:
            try: summary = _compare_artifacts(previous, artifact)
            except HTTPException: summary = {"added": 0, "removed": 0, "error": True}
        rows.append({"artifact": artifact, "run": run, "previous": previous, "summary": summary})
    return rows


@router.get("/devices/{device_id}/configuration/history", response_class=HTMLResponse, name="mikrotik_config_history")
def config_history(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user: return core.login_redirect()
        if not core.has_permission(user, "backup.read"): raise HTTPException(403)
        device = db.get(Device, device_id)
        if not device or device.vendor != "mikrotik": raise HTTPException(404)
        return core.render(request, db, user, "mikrotik_config_history.html", device=device, history=_history_rows(db, device), baseline_id=_baseline_id(device), drift_issue=_open_drift_issue(db, device.id))


@router.post("/devices/{device_id}/configuration/history/{artifact_id}/baseline", name="mikrotik_config_baseline_set")
def set_config_baseline(request: Request, device_id: uuid.UUID, artifact_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "backup.configure")
        device = db.get(Device, device_id)
        if not device or device.vendor != "mikrotik": raise HTTPException(404)
        if not _artifact_for_device(db, device.id, artifact_id): raise HTTPException(404, "Export non disponibile per questo apparato.")
        inventory = dict(device.inventory_data or {}); inventory[BASELINE_KEY] = str(artifact_id); inventory[BASELINE_AT_KEY] = utcnow().isoformat(); device.inventory_data = inventory
        issue = _open_drift_issue(db, device.id)
        if issue: issue.status = "resolved"; issue.resolved_at = utcnow(); issue.updated_at = utcnow()
        core.add_event(db, "CONFIG_BASELINE_APPROVED", actor=user, customer_id=device.customer_id, device_id=device.id, details={"artifact_id": str(artifact_id)}, source="portal")
        db.commit()
    return RedirectResponse(f"/devices/{device_id}/configuration/history?baseline=updated", status_code=303)


def _install_backup_completion_hook():
    import app.mikrotik_backup_agent as backup_agent
    if getattr(backup_agent, "_config_drift_hooked", False):
        return
    original = backup_agent.finalize_backup_job

    def finalize_with_drift(db, device, job, success, error=None):
        original(db, device, job, success, error)
        if not success:
            return
        raw_run_id = (job.payload or {}).get("run_id")
        try: run_id = uuid.UUID(str(raw_run_id))
        except (TypeError, ValueError): return
        artifact = db.scalar(select(BackupArtifact).where(BackupArtifact.run_id == run_id, BackupArtifact.artifact_type == "mikrotik_export", BackupArtifact.deleted_at.is_(None)).order_by(BackupArtifact.created_at.desc()))
        if artifact:
            evaluate_config_drift(db, device, artifact)

    backup_agent.finalize_backup_job = finalize_with_drift
    backup_agent._config_drift_hooked = True


def install_config_drift(app):
    _install_backup_completion_hook()
    app.include_router(router)
