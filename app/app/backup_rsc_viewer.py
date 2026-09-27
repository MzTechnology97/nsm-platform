import difflib
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select

from app import main as core
from app.backup_models import BackupArtifact
from app.backup_storage import resolve_artifact_path
from app.db import SessionLocal
from app.models import BackupRun, Device

router = APIRouter()
MAX_RSC_VIEW_BYTES = 2 * 1024 * 1024
MAX_DIFF_LINES = 12000


def _is_rsc(artifact: BackupArtifact) -> bool:
    return artifact.artifact_type == "mikrotik_export" or artifact.filename.lower().endswith(".rsc")


def _read_rsc(artifact: BackupArtifact) -> tuple[Path, str]:
    if not _is_rsc(artifact):
        raise HTTPException(415, "Questo artefatto non è un export RouterOS testuale.")
    try:
        path = resolve_artifact_path(artifact.storage_path)
    except ValueError:
        raise HTTPException(404, "File backup non disponibile.")
    if not path.is_file():
        raise HTTPException(404, "File backup non disponibile sullo storage.")
    size = path.stat().st_size
    if size > MAX_RSC_VIEW_BYTES:
        raise HTTPException(413, "Export troppo grande per la visualizzazione web.")
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    return path, text


def _context(db, artifact_id: uuid.UUID):
    artifact = db.get(BackupArtifact, artifact_id)
    if not artifact or artifact.deleted_at:
        raise HTTPException(404)
    run = db.get(BackupRun, artifact.run_id)
    if not run:
        raise HTTPException(404)
    device = db.get(Device, run.device_id)
    if not device:
        raise HTTPException(404)
    return artifact, run, device


def _candidate_exports(db, device_id: uuid.UUID, exclude_id: uuid.UUID):
    rows = db.execute(
        select(BackupArtifact, BackupRun)
        .join(BackupRun, BackupRun.id == BackupArtifact.run_id)
        .where(
            BackupRun.device_id == device_id,
            BackupArtifact.deleted_at.is_(None),
            BackupArtifact.id != exclude_id,
            or_(
                BackupArtifact.artifact_type == "mikrotik_export",
                BackupArtifact.filename.ilike("%.rsc"),
            ),
        )
        .order_by(BackupArtifact.created_at.desc())
        .limit(50)
    ).all()
    return rows


def _diff_rows(old_text: str, new_text: str):
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    if len(old_lines) + len(new_lines) > MAX_DIFF_LINES:
        raise HTTPException(413, "Export troppo estesi per il confronto web.")
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    rows = []
    summary = {"added": 0, "removed": 0, "unchanged": 0}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, line in enumerate(old_lines[i1:i2]):
                rows.append({"kind": "same", "old": i1 + offset + 1, "new": j1 + offset + 1, "text": line})
                summary["unchanged"] += 1
        elif tag == "delete":
            for offset, line in enumerate(old_lines[i1:i2]):
                rows.append({"kind": "removed", "old": i1 + offset + 1, "new": None, "text": line})
                summary["removed"] += 1
        elif tag == "insert":
            for offset, line in enumerate(new_lines[j1:j2]):
                rows.append({"kind": "added", "old": None, "new": j1 + offset + 1, "text": line})
                summary["added"] += 1
        elif tag == "replace":
            for offset, line in enumerate(old_lines[i1:i2]):
                rows.append({"kind": "removed", "old": i1 + offset + 1, "new": None, "text": line})
                summary["removed"] += 1
            for offset, line in enumerate(new_lines[j1:j2]):
                rows.append({"kind": "added", "old": None, "new": j1 + offset + 1, "text": line})
                summary["added"] += 1
    return rows, summary


@router.get("/operations/backups/artifacts/{artifact_id}/view", response_class=HTMLResponse, name="backup_rsc_view")
def backup_rsc_view(request: Request, artifact_id: uuid.UUID, against: uuid.UUID | None = None):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)

        artifact, run, device = _context(db, artifact_id)
        _path, current_text = _read_rsc(artifact)
        candidates = _candidate_exports(db, device.id, artifact.id)

        against_artifact = None
        against_run = None
        diff_rows = None
        diff_summary = None
        if against:
            against_artifact, against_run, against_device = _context(db, against)
            if against_device.id != device.id:
                raise HTTPException(400, "È possibile confrontare solo export dello stesso apparato.")
            _other_path, other_text = _read_rsc(against_artifact)
            diff_rows, diff_summary = _diff_rows(other_text, current_text)

        core.add_event(
            db,
            "BACKUP_RSC_VIEWED" if not against else "BACKUP_RSC_DIFF_VIEWED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={
                "artifact_id": str(artifact.id),
                "against_artifact_id": str(against_artifact.id) if against_artifact else None,
            },
            source="portal",
        )
        db.commit()

        return core.render(
            request,
            db,
            user,
            "backup_rsc_view.html",
            artifact=artifact,
            run=run,
            device=device,
            lines=current_text.splitlines(),
            candidates=candidates,
            against_artifact=against_artifact,
            against_run=against_run,
            diff_rows=diff_rows,
            diff_summary=diff_summary,
        )


def install_backup_rsc_viewer(app):
    app.include_router(router)
