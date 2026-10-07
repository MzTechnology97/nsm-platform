import base64
import binascii
import hashlib
import os
import re
import secrets
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_core import _effective_policy, _policy_settings
from app.backup_models import BackupArtifact, BackupPolicySettings
from app.backup_storage import remove_artifact_file, resolve_artifact_path, storage_root
from app.db import SessionLocal
from app.mikrotik_agent import _authenticate_agent, _json_body
from app.mikrotik_backup_models import BackupUploadSession, MikrotikBackupJobSecret
from app.models import BackupPolicy, BackupRun, Device, utcnow
from app.secret_vault import decrypt_text, encrypt_text
from app.security import validate_csrf

router = APIRouter()
MAX_BACKUP_BYTES = 64 * 1024 * 1024
MAX_CHUNK_BYTES = 32 * 1024
RECOMMENDED_CHUNK_BYTES = 24 * 1024
ARTIFACT_EXTENSIONS = {
    "mikrotik_binary": ".backup",
    "mikrotik_export": ".rsc",
}


def _device_policy(db, device: Device):
    policies = list(
        db.scalars(
            select(BackupPolicy).where(BackupPolicy.is_enabled.is_(True))
        )
    )
    settings_map = {
        policy.id: _policy_settings(db, policy, create=True) for policy in policies
    }
    return _effective_policy(device, policies, settings_map), settings_map


def _backup_formats(settings: BackupPolicySettings | None):
    options = dict(settings.options or {}) if settings else {}
    formats = []
    if options.get("mikrotik_binary", True):
        formats.append("mikrotik_binary")
    if options.get("mikrotik_export", True):
        formats.append("mikrotik_export")
    return formats


def _require_backup_job(db, device: Device, job_id: uuid.UUID):
    job = db.get(DeviceJob, job_id)
    if not job or job.device_id != device.id or job.job_type != "backup_mikrotik":
        raise HTTPException(404, "Backup job non trovato.")
    if job.status in {"success", "failed", "cancelled"}:
        raise HTTPException(409, "Backup job già concluso.")
    if job.status == "pending":
        # Re-queued for retry (or never delivered): only the Agent attempt that
        # receives the job from the next heartbeat may use it.
        raise HTTPException(409, "Backup job non consegnato all'agent.")
    return job


def _run_for_job(db, job: DeviceJob):
    raw = (job.payload or {}).get("run_id")
    try:
        run_id = uuid.UUID(str(raw))
    except (TypeError, ValueError):
        raise HTTPException(500, "Backup job privo di run associato.")
    run = db.get(BackupRun, run_id)
    if not run or run.device_id != job.device_id:
        raise HTTPException(500, "Backup run non valido.")
    return run


def _is_new_attempt(job: DeviceJob, upload: BackupUploadSession) -> bool:
    """True when the job was re-delivered after ``upload`` completed (retry)."""
    return bool(
        upload.completed_at
        and job.delivered_at
        and job.delivered_at > upload.completed_at
    )


def _supersede_previous_artifacts(
    db, run: BackupRun, artifact_type: str, now, keep_path: Path
) -> list[str]:
    """Retire artifacts of ``artifact_type`` left in ``run`` by an earlier attempt.

    ``keep_path`` is the file just archived; two attempts in the same second
    share a filename, so the replacement must never be removed with them.
    """
    previous = list(
        db.scalars(
            select(BackupArtifact).where(
                BackupArtifact.run_id == run.id,
                BackupArtifact.artifact_type == artifact_type,
                BackupArtifact.deleted_at.is_(None),
            )
        )
    )
    for artifact in previous:
        try:
            same_file = resolve_artifact_path(artifact.storage_path) == keep_path.resolve()
        except ValueError:
            same_file = False
        if not same_file:
            remove_artifact_file(artifact.storage_path)
        artifact.deleted_at = now
    return [str(artifact.id) for artifact in previous]


def _safe_backup_filename(device: Device, job: DeviceJob, artifact_type: str):
    extension = ARTIFACT_EXTENSIONS[artifact_type]
    label = device.display_name or device.device_identity or device.name or "mikrotik"
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-._")[:60] or "mikrotik"
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{label}_{str(job.id)[:8]}{extension}"


def _relative_storage_path(path: Path):
    return str(path.resolve().relative_to(storage_root()))


def _hash_file(path: Path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


@router.post("/devices/{device_id}/backup-now")
async def queue_mikrotik_backup(request: Request, device_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "backup.execute")
        device = db.scalar(
            select(Device)
            .where(Device.id == device_id)
            .options(selectinload(Device.customer), selectinload(Device.site))
        )
        if not device:
            raise HTTPException(404)
        if device.vendor != "mikrotik":
            raise HTTPException(400, "Backup agent disponibile solo per MikroTik in questa fase.")
        credential = db.scalar(
            select(DeviceAgentCredential).where(
                DeviceAgentCredential.device_id == device.id,
                DeviceAgentCredential.agent_type == "mikrotik_agent",
                DeviceAgentCredential.is_active.is_(True),
            )
        )
        if not credential:
            raise HTTPException(409, "Il MikroTik deve completare l'enrollment agent prima del backup.")
        existing = db.scalar(
            select(DeviceJob.id).where(
                DeviceJob.device_id == device.id,
                DeviceJob.job_type == "backup_mikrotik",
                DeviceJob.status.in_(["pending", "delivered", "running"]),
            )
        )
        if existing:
            return RedirectResponse(f"/devices/{device.id}?backup=already_pending#backups", status_code=303)

        policy, settings_map = _device_policy(db, device)
        if not policy:
            raise HTTPException(409, "Nessuna backup policy effettiva per questo apparato.")
        policy_settings = settings_map.get(policy.id)
        formats = _backup_formats(policy_settings)
        if not formats:
            raise HTTPException(409, "La policy non abilita alcun formato MikroTik.")

        run = BackupRun(
            device_id=device.id,
            policy_id=policy.id,
            status="pending",
            backup_type="mikrotik_multi" if len(formats) > 1 else formats[0],
        )
        db.add(run)
        db.flush()
        job = DeviceJob(
            device_id=device.id,
            job_type="backup_mikrotik",
            status="pending",
            payload={
                "run_id": str(run.id),
                "formats": formats,
                "cleanup_router_files": True,
            },
        )
        db.add(job)
        db.flush()
        backup_password = secrets.token_urlsafe(24)
        db.add(
            MikrotikBackupJobSecret(
                job_id=job.id,
                encrypted_backup_password=encrypt_text(backup_password),
            )
        )
        core.add_event(
            db,
            "BACKUP_JOB_QUEUED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={
                "job_id": str(job.id),
                "run_id": str(run.id),
                "policy_id": str(policy.id),
                "formats": formats,
            },
        )
        db.commit()
    return RedirectResponse(f"/devices/{device_id}?backup=queued#backups", status_code=303)


@router.post("/api/v1/agents/mikrotik/jobs/{job_id}/backup-config")
async def mikrotik_backup_config(request: Request, job_id: uuid.UUID):
    with SessionLocal() as db:
        device, _ = _authenticate_agent(db, request)
        job = _require_backup_job(db, device, job_id)
        secret = db.get(MikrotikBackupJobSecret, job.id)
        if not secret:
            raise HTTPException(500, "Password backup non disponibile.")
        try:
            backup_password = decrypt_text(secret.encrypted_backup_password)
        except ValueError:
            raise HTTPException(500, "Password backup non decifrabile.")
        return JSONResponse(
            {
                "status": "ok",
                "job_id": str(job.id),
                "formats": list((job.payload or {}).get("formats") or []),
                "backup_password": backup_password,
                "chunk_size": RECOMMENDED_CHUNK_BYTES,
            },
            headers={"Cache-Control": "no-store"},
        )


@router.post("/api/v1/agents/mikrotik/jobs/{job_id}/artifacts/start")
async def mikrotik_artifact_start(request: Request, job_id: uuid.UUID):
    payload = await _json_body(request)
    artifact_type = str(payload.get("artifact_type", "")).strip()
    if artifact_type not in ARTIFACT_EXTENSIONS:
        raise HTTPException(400, "Tipo artefatto non valido.")
    try:
        expected_size = int(payload.get("size_bytes"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Dimensione file non valida.")
    if expected_size < 1:
        raise HTTPException(400, "File backup vuoto: RouterOS non ha prodotto dati da archiviare.")
    if expected_size > MAX_BACKUP_BYTES:
        raise HTTPException(413, "File backup oltre il limite configurato.")

    with SessionLocal() as db:
        device, _ = _authenticate_agent(db, request)
        job = _require_backup_job(db, device, job_id)
        formats = set((job.payload or {}).get("formats") or [])
        if artifact_type not in formats:
            raise HTTPException(400, "Artefatto non richiesto dal job.")

        upload = db.scalar(
            select(BackupUploadSession).where(
                BackupUploadSession.job_id == job.id,
                BackupUploadSession.artifact_type == artifact_type,
            )
        )
        incoming = storage_root() / ".incoming" / str(job.id)
        incoming.mkdir(parents=True, exist_ok=True)
        temp_path = incoming / f"{artifact_type}.part"
        if upload and upload.status == "complete" and not _is_new_attempt(job, upload):
            raise HTTPException(409, "Artefatto già completato.")
        temp_path.write_bytes(b"")
        if upload:
            # A retried attempt regenerates every format. The artifact completed
            # by the previous attempt stays archived until the new upload
            # finishes, so a failing retry never destroys existing evidence.
            upload.expected_size = expected_size
            upload.received_size = 0
            upload.status = "receiving"
            upload.sha256 = None
            upload.completed_at = None
            upload.temp_path = _relative_storage_path(temp_path)
            upload.filename = _safe_backup_filename(device, job, artifact_type)
        else:
            upload = BackupUploadSession(
                job_id=job.id,
                device_id=device.id,
                artifact_type=artifact_type,
                filename=_safe_backup_filename(device, job, artifact_type),
                temp_path=_relative_storage_path(temp_path),
                expected_size=expected_size,
                received_size=0,
                status="receiving",
            )
            db.add(upload)
        job.status = "running"
        db.commit()
        return {"status": "ok", "upload_id": str(upload.id), "next_offset": 0}


@router.post("/api/v1/agents/mikrotik/uploads/{upload_id}/chunk")
async def mikrotik_artifact_chunk(request: Request, upload_id: uuid.UUID):
    payload = await _json_body(request)
    try:
        offset = int(payload.get("offset"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Offset non valido.")
    encoded = payload.get("data")
    if not isinstance(encoded, str):
        raise HTTPException(400, "Chunk mancante.")
    try:
        chunk = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Chunk base64 non valido.")
    if not chunk:
        raise HTTPException(400, "Chunk vuoto.")
    if len(chunk) > MAX_CHUNK_BYTES:
        raise HTTPException(413, "Chunk troppo grande.")

    with SessionLocal() as db:
        device, _ = _authenticate_agent(db, request)
        upload = db.get(BackupUploadSession, upload_id)
        if not upload or upload.device_id != device.id:
            raise HTTPException(404, "Upload non trovato.")
        if upload.status != "receiving":
            raise HTTPException(409, "Upload non in ricezione.")
        if offset != upload.received_size:
            return JSONResponse(
                {"status": "offset_mismatch", "next_offset": upload.received_size},
                status_code=409,
            )
        path = resolve_artifact_path(upload.temp_path)
        actual_size = path.stat().st_size if path.exists() else 0
        if actual_size != upload.received_size:
            raise HTTPException(409, "Stato upload incoerente.")
        if upload.expected_size is not None and offset + len(chunk) > upload.expected_size:
            raise HTTPException(400, "Chunk oltre la dimensione dichiarata.")
        with path.open("ab") as fh:
            fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        upload.received_size += len(chunk)
        db.commit()
        return {"status": "ok", "next_offset": upload.received_size}


@router.post("/api/v1/agents/mikrotik/uploads/{upload_id}/finish")
async def mikrotik_artifact_finish(request: Request, upload_id: uuid.UUID):
    with SessionLocal() as db:
        device, _ = _authenticate_agent(db, request)
        upload = db.get(BackupUploadSession, upload_id)
        if not upload or upload.device_id != device.id:
            raise HTTPException(404, "Upload non trovato.")
        if upload.status != "receiving":
            raise HTTPException(409, "Upload non in ricezione.")
        job = _require_backup_job(db, device, upload.job_id)
        run = _run_for_job(db, job)
        temp_path = resolve_artifact_path(upload.temp_path)
        if not temp_path.is_file():
            raise HTTPException(409, "File temporaneo non disponibile.")
        actual_size, sha256 = _hash_file(temp_path)
        if upload.expected_size is not None and actual_size != upload.expected_size:
            raise HTTPException(409, "Dimensione ricevuta diversa da quella dichiarata.")

        now = utcnow()
        final_dir = storage_root() / "devices" / str(device.id) / now.strftime("%Y") / now.strftime("%m")
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / upload.filename
        os.replace(temp_path, final_path)
        superseded = _supersede_previous_artifacts(db, run, upload.artifact_type, now, final_path)
        artifact = BackupArtifact(
            run_id=run.id,
            artifact_type=upload.artifact_type,
            filename=upload.filename,
            storage_path=_relative_storage_path(final_path),
            size_bytes=actual_size,
            sha256=sha256,
        )
        db.add(artifact)
        upload.status = "complete"
        upload.received_size = actual_size
        upload.sha256 = sha256
        upload.completed_at = now
        db.flush()
        run.status = "in_progress"
        run.size_bytes = sum(
            db.scalars(
                select(BackupArtifact.size_bytes).where(
                    BackupArtifact.run_id == run.id,
                    BackupArtifact.deleted_at.is_(None),
                )
            )
        )
        core.add_event(
            db,
            "BACKUP_ARTIFACT_RECEIVED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={
                "job_id": str(job.id),
                "run_id": str(run.id),
                "artifact_type": upload.artifact_type,
                "filename": upload.filename,
                "size_bytes": actual_size,
                "sha256": sha256,
                "superseded_artifact_ids": superseded,
            },
            source="mikrotik_agent",
        )
        db.commit()
        return {
            "status": "ok",
            "artifact_id": str(artifact.id),
            "size_bytes": actual_size,
            "sha256": sha256,
        }


def finalize_backup_job(db, device: Device, job: DeviceJob, success: bool, error: str | None = None):
    if job.job_type != "backup_mikrotik":
        return
    run = _run_for_job(db, job)
    uploads = list(
        db.scalars(select(BackupUploadSession).where(BackupUploadSession.job_id == job.id))
    )
    expected = set((job.payload or {}).get("formats") or [])
    completed = {item.artifact_type for item in uploads if item.status == "complete"}
    if success and completed != expected:
        success = False
        missing = sorted(expected - completed)
        error = f"Artefatti mancanti: {', '.join(missing)}"
    run.status = "success" if success else "failed"
    run.completed_at = utcnow()
    run.error_message = error
    if success:
        hashes = sorted(item.sha256 for item in uploads if item.sha256)
        if len(hashes) == 1:
            run.sha256 = hashes[0]
    core.add_event(
        db,
        "BACKUP_COMPLETED" if success else "BACKUP_FAILED",
        customer_id=device.customer_id,
        device_id=device.id,
        details={
            "job_id": str(job.id),
            "run_id": str(run.id),
            "artifacts": sorted(completed),
            "error": error,
        },
        severity="info" if success else "high",
        result="success" if success else "failed",
        source="mikrotik_agent",
    )


def install_mikrotik_backup(app):
    app.include_router(router)
