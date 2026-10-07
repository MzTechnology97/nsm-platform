"""MTK-03 — restore-test evidence for archived backup artifacts.

NSM proves that stored backups remain operationally useful without ever
restoring a production Device automatically.  The operator restores an artifact
in a controlled target (isolated lab, spare device or configuration review) and
records the outcome.  At recording time NSM re-verifies the stored file (presence,
size, SHA-256) so a test can only be recorded as *passed* against an intact
artifact.  Failed or partial tests raise an Action Center issue that the next
passed test for the same Device resolves.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import main as core
from app.backup_models import BackupArtifact
from app.backup_storage import resolve_artifact_path
from app.config import settings
from app.db import SessionLocal
from app.device_backup_explorer import _artifact_browser_error, _artifact_for_device, _device
from app.models import ActionIssue, BackupRun, Notification, utcnow
from app.restore_test_models import BackupRestoreTest
from app.security import validate_csrf
from app.ui_feedback import exception_message, flash_redirect

router = APIRouter()
METHODS = {
    "isolated_lab": "Laboratorio isolato (CHR / apparato di test)",
    "spare_device": "Apparato di scorta fuori produzione",
    "config_review": "Revisione configurazione importata",
}
RESULTS = {
    "passed": "Superato",
    "partial": "Parziale",
    "failed": "Non superato",
}
ISSUE_TITLE = "Restore test backup non superato"
MAX_NOTES = 4000
_RSC_VERSION_RE = re.compile(r"by RouterOS\s+([0-9][^\s;]*)", re.IGNORECASE)


def evaluate_artifact_integrity(artifact: BackupArtifact) -> dict:
    """Re-verify the stored artifact file against its archived evidence."""
    report = {
        "checked_at": utcnow().isoformat(),
        "file_present": False,
        "size_matches": False,
        "sha256_matches": False,
        "recomputed_sha256": None,
        "ok": False,
    }
    try:
        path = resolve_artifact_path(artifact.storage_path)
    except ValueError:
        report["error"] = "Percorso artefatto fuori dallo storage backup."
        return report
    if not path.is_file():
        report["error"] = "File non presente sullo storage."
        return report
    report["file_present"] = True
    size = path.stat().st_size
    report["size_matches"] = artifact.size_bytes is None or size == artifact.size_bytes
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    report["recomputed_sha256"] = digest.hexdigest()
    report["sha256_matches"] = bool(
        artifact.sha256 and artifact.sha256.lower() == report["recomputed_sha256"]
    )
    report["ok"] = report["size_matches"] and report["sha256_matches"]
    if not report["ok"]:
        report["error"] = "Dimensione o SHA-256 non corrispondono all'evidenza archiviata."
    return report


def source_routeros_version(artifact: BackupArtifact) -> str | None:
    """RouterOS version declared in a `.rsc` export header, when present."""
    if not (artifact.filename or "").lower().endswith(".rsc"):
        return None
    try:
        path = resolve_artifact_path(artifact.storage_path)
        head = path.read_bytes()[:4096].decode("utf-8-sig", errors="ignore")
    except (ValueError, OSError):
        return None
    match = _RSC_VERSION_RE.search(head)
    return match.group(1)[:80] if match else None


def _parse_performed_at(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        return utcnow()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(400, "Data/ora del test non valida.")
    if parsed.tzinfo is None:
        # `datetime-local` inputs carry the operator's wall-clock time.
        try:
            local_tz = ZoneInfo(settings.app_timezone)
        except Exception:
            local_tz = timezone.utc
        parsed = parsed.replace(tzinfo=local_tz)
    parsed = parsed.astimezone(timezone.utc)
    if parsed > utcnow():
        raise HTTPException(400, "La data del test non può essere nel futuro.")
    return parsed


def _sync_issue(db, device, test: BackupRestoreTest, now) -> None:
    open_issues = list(
        db.scalars(
            select(ActionIssue).where(
                ActionIssue.device_id == device.id,
                ActionIssue.category == "backup",
                ActionIssue.title == ISSUE_TITLE,
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        )
    )
    if test.result == "passed":
        for issue in open_issues:
            issue.status = "resolved"
            issue.resolved_at = now
            issue.updated_at = now
        for notification in db.scalars(
            select(Notification).where(
                Notification.device_id == device.id,
                Notification.category == "backup",
                Notification.title == ISSUE_TITLE,
                Notification.is_active.is_(True),
            )
        ):
            notification.is_active = False
        return
    details = {
        "message": f"Restore test {RESULTS[test.result].lower()} su {test.target_label}.",
        "restore_test_id": str(test.id),
        "artifact_filename": test.artifact_filename,
    }
    if open_issues:
        open_issues[0].details = details
        open_issues[0].updated_at = now
        return
    severity = "high" if test.result == "failed" else "warning"
    db.add(
        ActionIssue(
            category="backup",
            severity=severity,
            status="open",
            title=ISSUE_TITLE,
            details=details,
            customer_id=device.customer_id,
            device_id=device.id,
        )
    )
    db.add(
        Notification(
            severity=severity,
            category="backup",
            title=ISSUE_TITLE,
            message=details["message"],
            customer_id=device.customer_id,
            device_id=device.id,
            source_url=f"/devices/{device.id}/backups/restore-tests",
            is_active=True,
        )
    )


def latest_tests_by_artifact(db, artifact_ids) -> dict[uuid.UUID, BackupRestoreTest]:
    ids = list(artifact_ids)
    if not ids:
        return {}
    latest: dict[uuid.UUID, BackupRestoreTest] = {}
    for test in db.scalars(
        select(BackupRestoreTest)
        .where(BackupRestoreTest.artifact_id.in_(ids))
        .order_by(BackupRestoreTest.performed_at.desc())
    ):
        latest.setdefault(test.artifact_id, test)
    return latest


def latest_test_for_device(db, device_id) -> BackupRestoreTest | None:
    return db.scalar(
        select(BackupRestoreTest)
        .where(BackupRestoreTest.device_id == device_id)
        .order_by(BackupRestoreTest.performed_at.desc())
        .limit(1)
    )


@router.get(
    "/devices/{device_id}/backups/artifacts/{artifact_id}/restore-test",
    response_class=HTMLResponse,
    name="backup_restore_test_form",
)
def restore_test_form(request: Request, device_id: uuid.UUID, artifact_id: uuid.UUID):
    try:
        with SessionLocal() as db:
            user = core.current_user(request, db)
            if not user:
                return core.login_redirect()
            if not core.has_permission(user, "backup.read"):
                raise HTTPException(403)
            device = _device(db, device_id)
            artifact, run = _artifact_for_device(db, device.id, artifact_id)
            history = list(
                db.scalars(
                    select(BackupRestoreTest)
                    .where(BackupRestoreTest.artifact_id == artifact.id)
                    .order_by(BackupRestoreTest.performed_at.desc())
                )
            )
            return core.render(
                request,
                db,
                user,
                "backup_restore_test.html",
                device=device,
                artifact=artifact,
                run=run,
                integrity=evaluate_artifact_integrity(artifact),
                source_version=source_routeros_version(artifact),
                history=history,
                methods=METHODS,
                results=RESULTS,
                can_record=core.has_permission(user, "backup.execute"),
            )
    except HTTPException as exc:
        return _artifact_browser_error(request, device_id, exc)


@router.post(
    "/devices/{device_id}/backups/artifacts/{artifact_id}/restore-test",
    name="backup_restore_test_record",
)
def restore_test_record(
    request: Request,
    device_id: uuid.UUID,
    artifact_id: uuid.UUID,
    csrf: str = Form(...),
    method: str = Form(""),
    target_label: str = Form(""),
    target_routeros_version: str = Form(""),
    result: str = Form(""),
    performed_at: str = Form(""),
    notes: str = Form(""),
):
    form_url = f"/devices/{device_id}/backups/artifacts/{artifact_id}/restore-test"
    try:
        validate_csrf(request, csrf)
        with SessionLocal() as db:
            user = core.require_permission(request, db, "backup.execute")
            device = _device(db, device_id)
            artifact, run = _artifact_for_device(db, device.id, artifact_id)
            if method not in METHODS:
                raise HTTPException(400, "Seleziona il metodo con cui è stato eseguito il restore test.")
            if result not in RESULTS:
                raise HTTPException(400, "Seleziona l'esito del restore test.")
            target = target_label.strip()
            if not target:
                raise HTTPException(400, "Indica il target del restore (es. CHR di laboratorio).")
            when = _parse_performed_at(performed_at)
            integrity = evaluate_artifact_integrity(artifact)
            if result == "passed" and not integrity["ok"]:
                raise HTTPException(
                    409,
                    "Integrità dell'artefatto non verificata: non è possibile registrare un restore test superato.",
                )
            now = utcnow()
            test = BackupRestoreTest(
                device_id=device.id,
                artifact_id=artifact.id,
                run_id=run.id,
                artifact_filename=artifact.filename,
                artifact_type=artifact.artifact_type,
                artifact_sha256=artifact.sha256,
                artifact_size_bytes=artifact.size_bytes,
                source_routeros_version=source_routeros_version(artifact),
                method=method,
                target_label=target[:200],
                target_routeros_version=target_routeros_version.strip()[:80] or None,
                result=result,
                integrity=integrity,
                notes=notes.strip()[:MAX_NOTES] or None,
                performed_at=when,
                recorded_at=now,
                performed_by_user_id=user.id,
            )
            db.add(test)
            db.flush()
            _sync_issue(db, device, test, now)
            core.add_event(
                db,
                "BACKUP_RESTORE_TEST_RECORDED",
                actor=user,
                customer_id=device.customer_id,
                device_id=device.id,
                details={
                    "restore_test_id": str(test.id),
                    "artifact_id": str(artifact.id),
                    "run_id": str(run.id),
                    "artifact_sha256": artifact.sha256,
                    "method": method,
                    "target": test.target_label,
                    "result": result,
                    "integrity_ok": integrity["ok"],
                },
                severity="info" if result == "passed" else "warning",
                result="success" if result == "passed" else "failed",
                source="portal",
            )
            db.commit()
    except HTTPException as exc:
        if exc.status_code in {400, 409}:
            return flash_redirect(
                request,
                form_url,
                "warning",
                exception_message(exc, "Restore test non registrato."),
                title="Restore test non registrato",
            )
        return _artifact_browser_error(request, device_id, exc)
    return flash_redirect(
        request,
        f"/devices/{device_id}/backups/restore-tests",
        "success" if result == "passed" else "warning",
        f"Restore test registrato con esito: {RESULTS[result]}.",
        title="Restore test registrato",
    )


@router.get(
    "/devices/{device_id}/backups/restore-tests",
    response_class=HTMLResponse,
    name="backup_restore_tests",
)
def restore_tests(request: Request, device_id: uuid.UUID):
    try:
        with SessionLocal() as db:
            user = core.current_user(request, db)
            if not user:
                return core.login_redirect()
            if not core.has_permission(user, "backup.read"):
                raise HTTPException(403)
            device = _device(db, device_id)
            tests = list(
                db.scalars(
                    select(BackupRestoreTest)
                    .where(BackupRestoreTest.device_id == device.id)
                    .order_by(BackupRestoreTest.performed_at.desc())
                    .limit(200)
                )
            )
            live_artifacts = {
                artifact_id
                for artifact_id in db.scalars(
                    select(BackupArtifact.id)
                    .join(BackupRun, BackupRun.id == BackupArtifact.run_id)
                    .where(BackupRun.device_id == device.id, BackupArtifact.deleted_at.is_(None))
                )
            }
            return core.render(
                request,
                db,
                user,
                "backup_restore_tests.html",
                device=device,
                tests=tests,
                live_artifacts=live_artifacts,
                methods=METHODS,
                results=RESULTS,
            )
    except HTTPException as exc:
        return _artifact_browser_error(request, device_id, exc)


def install_backup_restore_tests(app) -> None:
    app.include_router(router)
