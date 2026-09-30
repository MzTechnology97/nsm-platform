"""Maintenance for Agent jobs that can no longer be delivered.

Only pending, already-expired jobs are finalized here. Backup jobs are excluded
because their retry/finalization lifecycle is owned by backup_maintenance.
Delivered/running jobs are also intentionally left to their domain-specific
completion or timeout handling.
"""
from __future__ import annotations

from sqlalchemy import select

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.models import utcnow

EXCLUDED_JOB_TYPES = {"backup_mikrotik"}
EXPIRED_PENDING_ERROR = "Job scaduto prima della consegna all'Agent."


def expire_pending_jobs(now=None) -> int:
    """Fail pending non-backup jobs whose delivery TTL has expired."""
    now = now or utcnow()
    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(DeviceJob).where(
                    DeviceJob.status == "pending",
                    DeviceJob.expires_at.is_not(None),
                    DeviceJob.expires_at <= now,
                    DeviceJob.job_type.notin_(EXCLUDED_JOB_TYPES),
                )
            )
        )
        for job in jobs:
            job.status = "failed"
            job.last_error = EXPIRED_PENDING_ERROR
            job.completed_at = now
        db.commit()
        return len(jobs)
