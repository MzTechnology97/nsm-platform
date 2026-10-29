"""Core 0.44 MikroTik configuration history and drift detection.

Since Core 0.49.303 every change is attributed (NSM, an operator who logged in, unknown: ``config_attribution``), the
Agent scripts are left out of the comparison, and legacy-Agent backups are checked too. The Action Center issue opens
only for a new change that is unknown or made while someone logged in from a public address; a difference from the
baseline that stays the same no longer raises a new event every night.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app import config_attribution as attribution
from app import main as core
from app.backup_models import BackupArtifact
from app.backup_text_tools import _diff_rows, _read_export
from app.models import ActionIssue, BackupRun, Device, utcnow

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
    return attribution.strip_nsm_managed("\n".join(lines).strip() + "\n")


def _compare_detail(before: BackupArtifact, after: BackupArtifact):
    """``(changed sections, summary)`` between two exports, Agent scripts left out."""
    rows, summary = _diff_rows(_normalized_export(_read_export(before)), _normalized_export(_read_export(after)))
    return attribution.changed_sections(rows), summary


def _compare_artifacts(before: BackupArtifact, after: BackupArtifact):
    return _compare_detail(before, after)[1]


def _export_time(db, artifact: BackupArtifact, run: BackupRun | None = None):
    run = run or db.get(BackupRun, artifact.run_id)
    return (run.completed_at if run else None) or artifact.created_at or utcnow()


def evaluate_config_drift(db, device: Device, new_artifact: BackupArtifact):
    baseline_id = _baseline_id(device)
    previous = next(((artifact, run) for artifact, run in _exports(db, device.id, limit=3) if artifact.id != new_artifact.id), None)
    reference = None
    reference_kind = "previous"
    if baseline_id and baseline_id != new_artifact.id:
        row = _artifact_for_device(db, device.id, baseline_id)
        if row:
            reference = row[0]
            reference_kind = "baseline"
    if reference is None and previous:
        reference = previous[0]
    if reference is None:
        core.add_event(db, "CONFIG_BASELINE_CANDIDATE_CREATED", customer_id=device.customer_id, device_id=device.id, details={"artifact_id": str(new_artifact.id)}, source="backup_engine")
        return {"state": "first_export", "changed": False}
    try:
        summary = _compare_artifacts(reference, new_artifact)
        # What changed since the previous export, and who did it (the difference from the baseline may be old).
        sections = _compare_detail(previous[0], new_artifact)[0] if previous else {}
    except HTTPException as exc:
        core.add_event(db, "CONFIG_DRIFT_CHECK_FAILED", customer_id=device.customer_id, device_id=device.id, severity="warning", result="failed", details={"artifact_id": str(new_artifact.id), "reason": exc.detail}, source="backup_engine")
        return {"state": "error", "changed": False}
    changed = bool(summary["added"] or summary["removed"])
    issue = _open_drift_issue(db, device.id)
    who = None
    if sections:
        since, until = _export_time(db, *previous), _export_time(db, new_artifact)
        who = attribution.attribute(attribution.load_evidence(db, device.id, since, until), sections, since, until)
    base = {"reference_artifact_id": str(reference.id), "artifact_id": str(new_artifact.id), "reference_kind": reference_kind}
    if changed:
        last = who or ((issue.details or {}).get("attribution") if issue else None)
        details = {**base, "added": summary["added"], "removed": summary["removed"], "detected_at": utcnow().isoformat(), "attribution": last}
        if issue:
            issue.updated_at = utcnow(); issue.details = details; issue.severity = "warning"
        elif who and who["alert"]:
            db.add(ActionIssue(category=DRIFT_CATEGORY, severity="warning", status="open", title=DRIFT_TITLE, details=details, customer_id=device.customer_id, device_id=device.id))
        if who:
            core.add_event(db, "CONFIG_DRIFT_DETECTED", customer_id=device.customer_id, device_id=device.id, severity="warning" if who["alert"] else "info", details=details, source="backup_engine")
        else:
            core.add_event(db, "CONFIG_DRIFT_CHECKED", customer_id=device.customer_id, device_id=device.id, details={**base, "changed": True, "new_change": False}, source="backup_engine")
    else:
        if issue:
            issue.status = "resolved"; issue.resolved_at = utcnow(); issue.updated_at = utcnow()
        core.add_event(db, "CONFIG_DRIFT_CHECKED", customer_id=device.customer_id, device_id=device.id, details={**base, "changed": False}, source="backup_engine")
    return {"state": "changed" if changed else "unchanged", "changed": changed, "attribution": who, **summary}


def _history_rows(db, device: Device):
    exports = _exports(db, device.id, limit=50)
    times = [_export_time(db, artifact, run) for artifact, run in exports]
    evidence = attribution.load_evidence(db, device.id, min(times), max(times)) if len(exports) > 1 else None
    rows = []
    for index, (artifact, run) in enumerate(exports):
        previous = exports[index + 1][0] if index + 1 < len(exports) else None
        summary = who = None
        if previous:
            try:
                sections, summary = _compare_detail(previous, artifact)
                if sections:
                    who = attribution.attribute(evidence, sections, times[index + 1], times[index])
            except HTTPException:
                summary = {"added": 0, "removed": 0, "error": True}
        rows.append({"artifact": artifact, "run": run, "previous": previous, "summary": summary, "attribution": who})
    return rows


def set_baseline_record(db, user, device: Device, artifact_id: uuid.UUID) -> None:
    """Approve one export as the configuration baseline (the open drift issue is resolved), audited; commits.

    HTTPException 404 when the export does not belong to the device.
    """
    if not _artifact_for_device(db, device.id, artifact_id):
        raise HTTPException(404, "Export non disponibile per questo apparato.")
    inventory = dict(device.inventory_data or {})
    inventory[BASELINE_KEY] = str(artifact_id)
    inventory[BASELINE_AT_KEY] = utcnow().isoformat()
    device.inventory_data = inventory
    issue = _open_drift_issue(db, device.id)
    if issue:
        issue.status = "resolved"
        issue.resolved_at = utcnow()
        issue.updated_at = utcnow()
    core.add_event(db, "CONFIG_BASELINE_APPROVED", actor=user, customer_id=device.customer_id, device_id=device.id, details={"artifact_id": str(artifact_id)}, source="portal")
    db.commit()


def _hook(module):
    if getattr(module, "_config_drift_hooked", False):
        return
    original = module.finalize_backup_job

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

    module.finalize_backup_job = finalize_with_drift
    module._config_drift_hooked = True


def _install_backup_completion_hook():
    import app.mikrotik_backup_agent as backup_agent
    import app.mikrotik_legacy_backup as legacy_backup

    # Modern and legacy Agents finish their backups in their own modules (each imports the finalizer by value).
    _hook(backup_agent)
    _hook(legacy_backup)


def install_config_drift(app):
    _install_backup_completion_hook()
    app.include_router(router)
