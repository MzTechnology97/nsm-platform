import difflib
import re
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
_SECRET_RE = re.compile(
    r'(?i)(\b(?:password|secret|private-key|pre-shared-key|authentication-key|passphrase|community)\s*=\s*)("[^"]*"|\S+)'
)


def _load_export(db, artifact_id: uuid.UUID):
    artifact = db.get(BackupArtifact, artifact_id)
    if not artifact or artifact.deleted_at:
        raise HTTPException(404, "Artefatto non trovato.")
    if artifact.artifact_type != "mikrotik_export" and not artifact.filename.lower().endswith(".rsc"):
        raise HTTPException(415, "Il viewer è disponibile solo per export RouterOS testuali (.rsc).")
    run = db.get(BackupRun, artifact.run_id)
    if not run:
        raise HTTPException(404, "Esecuzione backup non trovata.")
    device = db.get(Device, run.device_id)
    if not device:
        raise HTTPException(404, "Apparato non trovato.")
    try:
        path = resolve_artifact_path(artifact.storage_path)
    except ValueError:
        raise HTTPException(404, "Percorso artefatto non valido.")
    if not path.is_file():
        raise HTTPException(404, "File backup non disponibile sullo storage.")
    size = path.stat().st_size
    if size > MAX_TEXT_ARTIFACT_BYTES:
        raise HTTPException(413, "Export troppo grande per la visualizzazione web.")
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise HTTPException(415, "Il file non sembra un export RouterOS testuale.")
    text = raw.decode("utf-8", errors="replace")
    return artifact, run, device, text


def _redact(text: str) -> str:
    return _SECRET_RE.sub(lambda m: m.group(1) + '"*** REDACTED ***"', text)


def _peer_exports(db, device_id: uuid.UUID, exclude_id: uuid.UUID):
    return list(
        db.execute(
            select(BackupArtifact, BackupRun)
            .join(BackupRun, BackupRun.id == BackupArtifact.run_id)
            .where(
                BackupRun.device_id == device_id,
                BackupArtifact.deleted_at.is_(None),
                BackupArtifact.artifact_type == "mikrotik_export",
                BackupArtifact.id != exclude_id,
            )
            .order_by(BackupRun.started_at.desc())
            .limit(30)
        ).all()
    )


@router.get(
    "/operations/backups/artifacts/{artifact_id}/view",
    response_class=HTMLResponse,
    name="backup_artifact_view",
)
def backup_artifact_view(request: Request, artifact_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)
        artifact, run, device, text = _load_export(db, artifact_id)
        peers = _peer_exports(db, device.id, artifact.id)
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
            "backup_artifact_view.html",
            artifact=artifact,
            run=run,
            device=device,
            content=_redact(text),
            peers=peers,
        )


@router.get(
    "/operations/backups/artifacts/{artifact_id}/diff",
    response_class=HTMLResponse,
    name="backup_artifact_diff",
)
def backup_artifact_diff(request: Request, artifact_id: uuid.UUID, other: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)
        left_artifact, left_run, device, left_text = _load_export(db, artifact_id)
        right_artifact, right_run, right_device, right_text = _load_export(db, other)
        if right_device.id != device.id:
            raise HTTPException(400, "Il confronto è consentito solo tra export dello stesso apparato.")
        left_lines = _redact(left_text).splitlines()
        right_lines = _redact(right_text).splitlines()
        diff_lines = list(
            difflib.unified_diff(
                left_lines,
                right_lines,
                fromfile=left_artifact.filename,
                tofile=right_artifact.filename,
                lineterm="",
                n=3,
            )
        )
        additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removals = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        core.add_event(
            db,
            "BACKUP_EXPORT_COMPARED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={
                "left_artifact_id": str(left_artifact.id),
                "right_artifact_id": str(right_artifact.id),
                "additions": additions,
                "removals": removals,
            },
            source="portal",
        )
        db.commit()
        return core.render(
            request,
            db,
            user,
            "backup_artifact_diff.html",
            device=device,
            left_artifact=left_artifact,
            left_run=left_run,
            right_artifact=right_artifact,
            right_run=right_run,
            diff_lines=diff_lines,
            additions=additions,
            removals=removals,
        )


def install_backup_artifact_tools(app):
    app.include_router(router)
