"""Terminal cleanup for MikroTik backup jobs.

Backup passwords are per-job secrets and partial upload files are transient. Once
a backup job reaches terminal finalization neither should remain available.

This is installed as a focused wrapper around the existing finalizer so the
modern completion endpoint and the legacy fail-closed path share the same
cleanup semantics without changing artifact/archive behavior.
"""
from __future__ import annotations

from sqlalchemy import select

from app import mikrotik_backup as backup
from app import mikrotik_backup_agent as backup_agent
from app import mikrotik_legacy_jobs as legacy_jobs
from app.backup_storage import remove_artifact_file
from app.mikrotik_backup_models import BackupUploadSession, MikrotikBackupJobSecret
from app.models import utcnow


def _cleanup_terminal_state(db, job) -> None:
    if job.job_type != "backup_mikrotik":
        return

    uploads = list(
        db.scalars(
            select(BackupUploadSession).where(BackupUploadSession.job_id == job.id)
        )
    )
    now = utcnow()
    for upload in uploads:
        if upload.status == "complete":
            continue
        remove_artifact_file(upload.temp_path)
        upload.status = "failed"
        upload.completed_at = now

    secret = db.get(MikrotikBackupJobSecret, job.id)
    if secret is not None:
        db.delete(secret)


def install_mikrotik_backup_finalization_cleanup() -> None:
    previous = backup.finalize_backup_job

    def wrapped(db, device, job, success: bool, error: str | None = None):
        result = previous(db, device, job, success, error)
        _cleanup_terminal_state(db, job)
        return result

    backup.finalize_backup_job = wrapped

    # These modules import the finalizer by value, so update their references as
    # well. This keeps one terminal-cleanup contract for all existing callers.
    if backup_agent.finalize_backup_job is previous:
        backup_agent.finalize_backup_job = wrapped
    if legacy_jobs.finalize_backup_job is previous:
        legacy_jobs.finalize_backup_job = wrapped
