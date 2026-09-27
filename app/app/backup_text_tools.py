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
MAX_DIFF_LINES = 20000


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


def _numbered(text: str) -> str:
    return "\n".join(f"{index:6}  {line}" for index, line in enumerate(text.splitlines(), start=1))


@router.get(
    "/operations/backups/artifacts/{artifact_id}/view",
    response_class=HTMLResponse,
    name="backup_artifact_text_view",
)
def backup_artifact_text_view(request: Request, artifact_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)
        artifact, run, device = _load_export(db, artifact_id)
        text = _read_export(artifact)
        candidates = _comparison_candidates(db, device.id, artifact.id)
        core.add_event(
            db,
            "BACKUP_EXPORT_VIEWED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={"artifact_id": str(artifact.id), "filename": artifact.filename},
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
            numbered_text=_numbered(text),
            comparison_candidates=candidates,
            diff_text=None,
            diff_stats=None,
            compare_artifact=None,
            compare_run=None,
        )


@router.get(
    "/operations/backups/artifacts/{artifact_id}/diff",
    response_class=HTMLResponse,
    name="backup_artifact_text_diff",
)
def backup_artifact_text_diff(request: Request, artifact_id: uuid.UUID, compare_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)
        artifact, run, device = _load_export(db, artifact_id)
        compare_artifact, compare_run, compare_device = _load_export(db, compare_id)
        if compare_device.id != device.id:
            raise HTTPException(400, "Il confronto è consentito solo tra backup dello stesso apparato.")

        current_text = _read_export(artifact)
        compare_text = _read_export(compare_artifact)
        diff_lines = list(
            difflib.unified_diff(
                compare_text.splitlines(),
                current_text.splitlines(),
                fromfile=compare_artifact.filename,
                tofile=artifact.filename,
                lineterm="",
                n=3,
            )
        )
        truncated = len(diff_lines) > MAX_DIFF_LINES
        shown_lines = diff_lines[:MAX_DIFF_LINES]
        additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        candidates = _comparison_candidates(db, device.id, artifact.id)
        core.add_event(
            db,
            "BACKUP_EXPORT_DIFF_VIEWED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={
                "artifact_id": str(artifact.id),
                "compare_artifact_id": str(compare_artifact.id),
                "additions": additions,
                "deletions": deletions,
                "truncated": truncated,
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
            numbered_text=_numbered(current_text),
            comparison_candidates=candidates,
            diff_text="\n".join(shown_lines),
            diff_stats={"additions": additions, "deletions": deletions, "truncated": truncated},
            compare_artifact=compare_artifact,
            compare_run=compare_run,
        )


def install_backup_text_tools(app):
    app.include_router(router)
