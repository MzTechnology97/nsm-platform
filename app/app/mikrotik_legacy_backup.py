"""`.rsc` export backup for RouterOS 7.x legacy Agents (7.12 family).

RouterOS 7.12 has no `/file read` chunking nor base64 conversion, but a script
can read a file of up to 60 KB with ``/file get ... contents`` and post it as
the plain-text body of a request.  The legacy Agent therefore exports the
configuration, waits for a settled file, refuses anything above the limit and
posts the text to NSM, which archives it with SHA-256 exactly like a modern
upload.  Binary `.backup` files and RouterOS 6 (4 KB contents limit) remain
unavailable: the capability says so instead of claiming protection.
"""
from __future__ import annotations

import os
import re
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent_module
from app import mikrotik_legacy as legacy
from app import mikrotik_legacy_jobs as legacy_jobs
from app.agent_models import DeviceJob
from app.backup_capabilities import LEGACY_EXPORT_MAX_BYTES, legacy_export_supported
from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.mikrotik_backup import (
    _hash_file,
    _relative_storage_path,
    _require_backup_job,
    _run_for_job,
    _safe_backup_filename,
    _supersede_previous_artifacts,
    finalize_backup_job,
)
from app.mikrotik_backup_models import BackupUploadSession
from app.models import utcnow

router = APIRouter()
UNSUPPORTED = (
    "Backup non eseguibile con questo agent legacy. Per il backup completo (binario + export) via FTP: "
    "./manage.sh legacy-backup-enable <IPv4 di NSM> e agent legacy 0.49.16+ con profilo completo."
)


def _handler(base_url: str, check_certificate: bool) -> str:
    cert = " check-certificate=yes" if check_certificate else ""
    artifact = f'("{base_url}/api/v1/agents/mikrotik/legacy/jobs/" . $nsmJobId . "/artifact?size=" . $nsmSize)'
    return f'''            :if ($nsmJobType = "backup_mikrotik") do={{
              :local nsmBaseName ("nsm-" . [:pick $nsmJobId 0 8])
              :local nsmName ($nsmBaseName . ".rsc")
              :do {{ /file remove [find where (name=$nsmName || name=("flash/" . $nsmName))] }} on-error={{}}
              /export file=$nsmBaseName
              :local nsmPath ""
              :local nsmSize 0
              :local nsmPrev 0
              :local nsmTicks 0
              :while (($nsmTicks < 60) && (($nsmSize = 0) || ($nsmSize != $nsmPrev))) do={{
                :delay 500ms
                :set nsmTicks ($nsmTicks + 1)
                :local nsmIds [/file find where (name=$nsmName || name=("flash/" . $nsmName))]
                :if ([:len $nsmIds] > 0) do={{
                  :set nsmPath [/file get ($nsmIds->0) name]
                  :set nsmPrev $nsmSize
                  :set nsmSize [:tonum [/file get ($nsmIds->0) size]]
                }}
              }}
              :local nsmText ""
              :if (($nsmSize > 0) && ($nsmSize = $nsmPrev) && ($nsmSize <= {LEGACY_EXPORT_MAX_BYTES})) do={{ :set nsmText [/file get [find where name=$nsmPath] contents] }}
              :do {{ /file remove [find where (name=$nsmName || name=("flash/" . $nsmName))] }} on-error={{}}
              :if ([:len $nsmPath] = 0) do={{ :error "NSM export file not created" }}
              :if ($nsmSize = 0) do={{ :error "NSM export file is empty" }}
              :if ($nsmSize != $nsmPrev) do={{ :error "NSM export size did not settle" }}
              :if ($nsmSize > {LEGACY_EXPORT_MAX_BYTES}) do={{ :error ("NSM export too large for the legacy transport: " . $nsmSize . " bytes") }}
              :if ([:len $nsmText] != $nsmSize) do={{ :error ("NSM export read incomplete: " . [:len $nsmText] . "/" . $nsmSize) }}
              :local nsmUp [/tool fetch url={artifact} http-method=post http-header-field=("Content-Type:text/plain," . $nsmLegacyHeaders) http-data=$nsmText output=user as-value{cert}]
              :if (($nsmUp->"data") != "ok") do={{ :error "NSM export upload rejected" }}
              :set nsmJobOutput ("export_bytes=" . $nsmSize)
            }}
'''


@router.post("/api/v1/agents/mikrotik/legacy/jobs/{job_id}/artifact", response_class=PlainTextResponse, name="mikrotik_legacy_backup_artifact")
async def legacy_artifact(request: Request, job_id: uuid.UUID, size: int = Query(...)):
    body = await request.body()
    if size < 1 or size > LEGACY_EXPORT_MAX_BYTES:
        raise HTTPException(400, "Dimensione export legacy non valida.")
    if len(body) != size:
        raise HTTPException(409, f"Export legacy incompleto: ricevuti {len(body)} di {size} byte.")
    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = _require_backup_job(db, device, job_id)
        if "mikrotik_export" not in set((job.payload or {}).get("formats") or []):
            raise HTTPException(400, "Export non richiesto dal job.")
        run = _run_for_job(db, job)
        now = utcnow()
        filename = _safe_backup_filename(device, job, "mikrotik_export")
        final_dir = storage_root() / "devices" / str(device.id) / now.strftime("%Y") / now.strftime("%m")
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / filename
        temp_path = final_dir / (filename + ".part")
        temp_path.write_bytes(body)
        os.replace(temp_path, final_path)
        actual_size, sha256 = _hash_file(final_path)
        superseded = _supersede_previous_artifacts(db, run, "mikrotik_export", now, final_path)
        upload = db.scalar(select(BackupUploadSession).where(BackupUploadSession.job_id == job.id, BackupUploadSession.artifact_type == "mikrotik_export"))
        if upload is None:
            upload = BackupUploadSession(job_id=job.id, device_id=device.id, artifact_type="mikrotik_export", filename=filename,
                                         temp_path=_relative_storage_path(final_path), expected_size=size, received_size=0, status="receiving")
            db.add(upload)
        upload.filename = filename
        upload.expected_size = size
        upload.received_size = actual_size
        upload.status = "complete"
        upload.sha256 = sha256
        upload.completed_at = now
        db.add(BackupArtifact(run_id=run.id, artifact_type="mikrotik_export", filename=filename,
                              storage_path=_relative_storage_path(final_path), size_bytes=actual_size, sha256=sha256))
        job.status = "running"
        run.status = "in_progress"
        run.size_bytes = actual_size
        core.add_event(
            db, "BACKUP_ARTIFACT_RECEIVED", customer_id=device.customer_id, device_id=device.id,
            details={"job_id": str(job.id), "run_id": str(run.id), "artifact_type": "mikrotik_export", "filename": filename,
                     "size_bytes": actual_size, "sha256": sha256, "superseded_artifact_ids": superseded, "transport": "routeros_legacy"},
            source="mikrotik_agent_legacy",
        )
        db.commit()
    return PlainTextResponse("ok", headers={"Cache-Control": "no-store"})


def _install_agent_handler():
    previous = legacy._legacy_agent_source

    def source_with_backup(base_url, device_id, raw_secret, check_certificate):
        source = previous(base_url, device_id, raw_secret, check_certificate)
        marker = '            :if ($nsmJobType = "snapshot_section") do={'
        if marker not in source:
            raise RuntimeError("MikroTik legacy backup extension point not found")
        return source.replace(marker, _handler(base_url, check_certificate) + marker, 1)

    legacy._legacy_agent_source = source_with_backup


def _install_delivery():
    previous_deferred = legacy_jobs._fail_deferred_jobs
    previous_line = legacy_jobs._job_line

    def fail_deferred(db, device, now):
        # Backup jobs stay deliverable when this legacy agent can export them.
        if legacy_export_supported(device):
            saved = legacy_jobs.LEGACY_DEFERRED_JOB_TYPES
            legacy_jobs.LEGACY_DEFERRED_JOB_TYPES = set(saved) - {"backup_mikrotik"}
            try:
                return previous_deferred(db, device, now)
            finally:
                legacy_jobs.LEGACY_DEFERRED_JOB_TYPES = saved
        rows = previous_deferred(db, device, now)
        for job in rows:
            if job.job_type == "backup_mikrotik":
                job.last_error = UNSUPPORTED
        # The session does not autoflush: without this the delivery query
        # below would still see the failed backup job as pending.
        db.flush()
        return rows

    def job_line(job):
        if job.job_type == "backup_mikrotik":
            formats = set((job.payload or {}).get("formats") or [])
            if formats != {"mikrotik_export"}:
                raise HTTPException(409, "Il trasporto legacy esporta solo .rsc.")
            return f"{job.id}|backup_mikrotik||"
        return previous_line(job)

    legacy_jobs._fail_deferred_jobs = fail_deferred
    legacy_jobs._job_line = job_line
    legacy_jobs.LEGACY_JOB_TYPES = set(legacy_jobs.LEGACY_JOB_TYPES) | {"backup_mikrotik"}


def _install_completion():
    previous = legacy_jobs.legacy_job_complete

    async def complete(request: Request, job_id: uuid.UUID, status: str = "failed"):
        with SessionLocal() as db:
            job = db.get(DeviceJob, job_id)
            job_type = job.job_type if job else None
        if job_type != "backup_mikrotik":
            return await previous(request, job_id, status)
        output = (await request.body()).decode("utf-8", errors="replace")[:4000]
        success = str(status).strip().lower() == "success"
        with SessionLocal() as db:
            device, _ = agent_module._authenticate_agent(db, request)
            job = db.get(DeviceJob, job_id)
            if not job or job.device_id != device.id:
                raise HTTPException(404, "Job legacy non trovato.")
            error = None if success else (output or "Export legacy fallito sull'apparato.")
            if error and re.search(r"too large", error):
                error = f"{error} — l'export supera 60 KB: aggiorna a RouterOS 7.13+ per il backup completo."
            job.status = "success" if success else "failed"
            job.result = {"output": output, "legacy_transport": True}
            job.last_error = error
            job.completed_at = utcnow()
            finalize_backup_job(db, device, job, success, error)
            # A reported success without the archived export is a failure.
            run = _run_for_job(db, job)
            if run.status != "success":
                job.status = "failed"
                job.last_error = run.error_message or error
            db.commit()
        return {"status": "ok"}

    legacy_jobs.legacy_job_complete = complete


def install_mikrotik_legacy_backup(app) -> None:
    _install_agent_handler()
    _install_delivery()
    _install_completion()
    app.include_router(router)
