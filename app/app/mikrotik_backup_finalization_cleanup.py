"""Terminal cleanup for MikroTik backup jobs.

Backup passwords are per-job secrets and partial upload files are transient. Once
a backup job reaches terminal finalization neither should remain available.

The application has multiple finalizer call sites and the modern Agent path may
already be wrapped by configuration-drift detection. Install cleanup around the
*current* callable for composed paths, while direct imports of the canonical
finalizer are repointed to one shared wrapper. This preserves existing hooks and
also covers the maintenance worker's timeout/failure path.
"""
from __future__ import annotations

from functools import wraps

from sqlalchemy import select

from app import backup_maintenance
from app import mikrotik_backup as backup
from app import mikrotik_backup_agent as backup_agent
from app import mikrotik_legacy_jobs as legacy_jobs
from app.backup_storage import remove_artifact_file
from app.mikrotik_backup_models import BackupUploadSession, MikrotikBackupJobSecret
from app.models import utcnow

_INSTALLED = False
_WRAPPER_MARKER = "_nsm_backup_terminal_cleanup"


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


def _with_terminal_cleanup(finalizer):
    if getattr(finalizer, _WRAPPER_MARKER, False):
        return finalizer

    @wraps(finalizer)
    def wrapped(db, device, job, success: bool, error: str | None = None):
        result = finalizer(db, device, job, success, error)
        _cleanup_terminal_state(db, job)
        return result

    setattr(wrapped, _WRAPPER_MARKER, True)
    return wrapped


def _patch_reference(module, previous, canonical) -> None:
    """Patch a by-value import without discarding wrappers already installed."""
    current = getattr(module, "finalize_backup_job", None)
    if current is None or getattr(current, _WRAPPER_MARKER, False):
        return
    if current is previous:
        module.finalize_backup_job = canonical
    else:
        # Example: config_drift wraps the modern Agent finalizer before this
        # installer runs. Wrap that composed callable instead of replacing it.
        module.finalize_backup_job = _with_terminal_cleanup(current)


def install_mikrotik_backup_finalization_cleanup() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    previous = backup.finalize_backup_job
    canonical = _with_terminal_cleanup(previous)
    backup.finalize_backup_job = canonical

    # These modules import the finalizer by value. The modern Agent reference can
    # already be the config-drift wrapper; legacy and maintenance are normally
    # direct references. In every case retain the existing behavior and add the
    # terminal cleanup exactly once.
    _patch_reference(backup_agent, previous, canonical)
    _patch_reference(legacy_jobs, previous, canonical)
    _patch_reference(backup_maintenance, previous, canonical)

    _INSTALLED = True
