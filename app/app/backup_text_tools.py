"""Safe text inspection tools for MikroTik export backups.

Only stored ``mikrotik_export`` artifacts are readable here. Paths are resolved
through the backup storage guard, file size is bounded, and comparisons are
restricted to exports belonging to the same device.
"""

import difflib
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import main as core
from app.backup_models import BackupArtifact
from app.backup_storage import resolve_artifact_path
from app.db import SessionLocal
from app.models import BackupRun, Device

router = APIRouter()
MAX_TEXT_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_DIFF_ROWS = 20000


def _load_export(db, artifact_id: uuid.UUID):
    artifact = db.get(BackupArtifact, artifact_id)
    if not artifact or artifact.deleted_at or artifact.artifact_type != "mikrotik_export":
        raise HTTPException(404, "Export testuale non disponibile.")
    run = db.get(BackupRun, artifact.run_id)
    if not run:
        raise HTTPException(404, "Esecuzione backup non disponibile.")
    device = db.get(Device, run.device_id)
    if not device:
        raise HTTPException(404, "Apparato non disponibile.")
    return artifact, run, device


def _read_export(artifact: BackupArtifact) -> str:
    try:
        path = resolve_artifact_path(artifact.storage_path)
    except ValueError:
        raise HTTPException(404, "Percorso backup non valido.")
    if not path.is_file():
        raise HTTPException(404, "File backup non disponibile sullo storage.")
    size = path.stat().st_size
    if size > MAX_TEXT_ARTIFACT_BYTES:
        raise HTTPException(413, "Export troppo grande per la visualizzazione web.")
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise HTTPException(415, "L'artefatto non sembra un export testuale RouterOS.")
    return raw.decode("utf-8", errors="replace")


def _comparison_candidates(db, device_id: uuid.UUID, current_id: uuid.UUID):
    return list(
        db.execute(
            select(BackupArtifact, BackupRun)
            .join(BackupRun, BackupRun.id == BackupArtifact.run_id)
            .where(
                BackupRun.device_id == device_id,
                BackupArtifact.artifact_type == "mikrotik_export",
                BackupArtifact.deleted_at.is_(None),
                BackupArtifact.id != current_id,
            )
            .order_by(BackupRun.started_at.desc(), BackupArtifact.created_at.desc())
            .limit(30)
        ).all()
    )


def _diff_rows(before: str, after: str):
    old_lines = before.splitlines()
    new_lines = after.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    rows = []
    added = removed = unchanged = 0

    def append(kind, old, new, text):
        if len(rows) < MAX_DIFF_ROWS:
            rows.append({"kind": kind, "old": old, "new": new, "text": text})

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, text in enumerate(old_lines[i1:i2]):
                unchanged += 1
                append("context", i1 + offset + 1, j1 + offset + 1, text)
        elif tag == "delete":
            for offset, text in enumerate(old_lines[i1:i2]):
                removed += 1
                append("removed", i1 + offset + 1, None, text)
        elif tag == "insert":
            for offset, text in enumerate(new_lines[j1:j2]):
                added += 1
                append("added", None, j1 + offset + 1, text)
        elif tag == "replace":
            for offset, text in enumerate(old_lines[i1:i2]):
                removed += 1
                append("removed", i1 + offset + 1, None, text)
            for offset, text in enumerate(new_lines[j1:j2]):
                added += 1
                append("added", None, j1 + offset + 1, text)

    return rows, {
        "added": added,
        "removed": removed,
        "unchanged": unchanged,
        "truncated": (added + removed + unchanged) > MAX_DIFF_ROWS,
    }


def _render_view(request, db, user, artifact, run, device, against_artifact=None, against_run=None):
    text = _read_export(artifact)
    candidates = _comparison_candidates(db, device.id, artifact.id)
    diff_rows = []
    diff_summary = None
    if against_artifact:
        before = _read_export(against_artifact)
        diff_rows, diff_summary = _diff_rows(before, text)
    return core.render(
        request,
        db,
        user,
        "backup_rsc_view.html",
        artifact=artifact,
        run=run,
        device=device,
        lines=text.splitlines(),
        candidates=candidates,
        against_artifact=against_artifact,
        against_run=against_run,
        diff_rows=diff_rows,
        diff_summary=diff_summary,
    )


@router.get(
    "/operations/backups/artifacts/{artifact_id}/view",
    response_class=HTMLResponse,
    name="backup_artifact_text_view",
)
def backup_artifact_text_view(request: Request, artifact_id: uuid.UUID, against: str = ""):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)
        artifact, run, device = _load_export(db, artifact_id)
        against_artifact = against_run = None
        if against:
            try:
                against_id = uuid.UUID(against)
            except ValueError:
                raise HTTPException(400, "Identificativo confronto non valido.")
            against_artifact, against_run, against_device = _load_export(db, against_id)
            if against_device.id != device.id:
                raise HTTPException(400, "Il confronto è consentito solo tra backup dello stesso apparato.")

        event_type = "BACKUP_EXPORT_DIFF_VIEWED" if against_artifact else "BACKUP_EXPORT_VIEWED"
        details = {"artifact_id": str(artifact.id), "filename": artifact.filename}
        if against_artifact:
            details["compare_artifact_id"] = str(against_artifact.id)
        core.add_event(
            db,
            event_type,
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details=details,
            source="portal",
        )
        db.commit()
        return _render_view(request, db, user, artifact, run, device, against_artifact, against_run)


@router.get(
    "/operations/backups/artifacts/{artifact_id}/diff",
    response_class=HTMLResponse,
    name="backup_artifact_text_diff",
)
def backup_artifact_text_diff(request: Request, artifact_id: uuid.UUID, compare_id: uuid.UUID):
    return backup_artifact_text_view(request, artifact_id, against=str(compare_id))


def install_backup_text_tools(app):
    app.include_router(router)
