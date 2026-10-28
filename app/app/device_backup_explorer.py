"""Device-scoped backup archive and file-browser actions.

Every query is constrained to one Device.  The workspace reuses the existing
backup engine and safe RouterOS text-inspection primitives while keeping binary
artifacts download-only and rejecting cross-device artifact identifiers.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import main as core
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_core import _effective_policy, _policy_settings
from app.backup_models import BackupArtifact
from app.backup_storage import resolve_artifact_path
from app.backup_text_tools import _comparison_candidates, _diff_rows, _read_export
from app.db import SessionLocal
from app.models import BackupPolicy, BackupRun, Device
from app.input_errors import InputError

router = APIRouter()
_ACTIVE_JOB_STATES = {"pending", "delivered", "running"}


def _device(db, device_id: uuid.UUID) -> Device:
    device = db.scalar(
        select(Device)
        .where(Device.id == device_id)
        .options(selectinload(Device.customer), selectinload(Device.site))
    )
    if not device:
        raise HTTPException(404, "Apparato non trovato.")
    return device


def _artifact_for_device(
    db, device_id: uuid.UUID, artifact_id: uuid.UUID
) -> tuple[BackupArtifact, BackupRun]:
    row = db.execute(
        select(BackupArtifact, BackupRun)
        .join(BackupRun, BackupRun.id == BackupArtifact.run_id)
        .where(
            BackupArtifact.id == artifact_id,
            BackupArtifact.deleted_at.is_(None),
            BackupRun.device_id == device_id,
        )
    ).first()
    if not row:
        raise HTTPException(404, "Backup non disponibile per questo apparato.")
    return row[0], row[1]


def _artifact_kind(artifact: BackupArtifact) -> str:
    return "export" if _is_human_readable(artifact) else "binary"


def _is_human_readable(artifact: BackupArtifact) -> bool:
    return artifact.artifact_type in ("mikrotik_export", "tr069_parameters") or artifact.filename.lower().endswith(".rsc")


def artifact_text_record(db, user, device: Device, artifact_id: uuid.UUID, against: str = "") -> dict:
    """Read a text export of the device, optionally diffed against an older one; audited, shared by both interfaces."""
    artifact, run = _artifact_for_device(db, device.id, artifact_id)
    if not _is_human_readable(artifact):
        raise InputError("binary", "Questo backup è binario e può essere solo scaricato.", 415)
    text = _read_export(artifact)
    candidates = _comparison_candidates(db, device.id, artifact.id, "tr069_parameters" if artifact.artifact_type == "tr069_parameters" else "mikrotik_export")
    against_artifact = against_run = None
    diff_rows = []
    diff_summary = None
    if against:
        try:
            against_id = uuid.UUID(against)
        except ValueError:
            raise InputError("compare_id", "Identificativo confronto non valido.")
        against_artifact, against_run = _artifact_for_device(db, device.id, against_id)
        if not _is_human_readable(against_artifact):
            raise InputError("compare_binary", "Il confronto richiede due backup testuali dello stesso apparato.")
        before = _read_export(against_artifact)
        diff_rows, diff_summary = _diff_rows(before, text)

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
    return {
        "artifact": artifact,
        "run": run,
        "text": text,
        "candidates": candidates,
        "against_artifact": against_artifact,
        "against_run": against_run,
        "diff_rows": diff_rows,
        "diff_summary": diff_summary,
    }


def _manual_backup_context(db, device: Device):
    policies = list(db.scalars(select(BackupPolicy)))
    settings_map = {
        policy.id: _policy_settings(db, policy, create=False) for policy in policies
    }
    policy = _effective_policy(device, policies, settings_map)
    credential = db.scalar(
        select(DeviceAgentCredential).where(
            DeviceAgentCredential.device_id == device.id,
            DeviceAgentCredential.agent_type == "mikrotik_agent",
            DeviceAgentCredential.is_active.is_(True),
        )
    )
    pending = bool(
        db.scalar(
            select(DeviceJob.id).where(
                DeviceJob.device_id == device.id,
                DeviceJob.job_type == "backup_mikrotik",
                DeviceJob.status.in_(_ACTIVE_JOB_STATES),
            )
        )
    )
    return policy, credential, pending


@router.get(
    "/devices/{device_id}/backups/artifacts/{artifact_id}/download",
    name="device_backup_artifact_download",
)
def device_backup_artifact_download(
    request: Request, device_id: uuid.UUID, artifact_id: uuid.UUID
):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.read"):
            raise HTTPException(403)
        device = _device(db, device_id)
        artifact, _run = _artifact_for_device(db, device.id, artifact_id)
        try:
            path = resolve_artifact_path(artifact.storage_path)
        except ValueError:
            raise HTTPException(404, "Percorso backup non valido.")
        if not path.is_file():
            raise HTTPException(404, "File backup non disponibile sullo storage.")

        core.add_event(
            db,
            "BACKUP_ARTIFACT_DOWNLOADED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={"artifact_id": str(artifact.id), "filename": artifact.filename},
            source="portal",
        )
        db.commit()
        media_type = (
            "text/plain; charset=utf-8"
            if _is_human_readable(artifact)
            else "application/octet-stream"
        )
        return FileResponse(path, filename=artifact.filename, media_type=media_type)


def install_device_backup_explorer(app) -> None:
    app.include_router(router)
