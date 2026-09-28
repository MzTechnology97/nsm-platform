"""Platform-wide periodic maintenance orchestration.

Core 0.36 moves orchestration out of the backup-specific module so agent health
can evolve independently while preserving all existing backup scheduling,
retry, recovery and retention behavior.
"""
from __future__ import annotations

from datetime import timezone

from app.agent_health_actions import sync_agent_health
from app.backup_maintenance import (
    apply_retention,
    recover_stale_jobs,
    resolve_recovered_backup_issues,
    schedule_due_backups,
)
from app.db import SessionLocal
from app.models import utcnow


def maintenance_tick(now=None):
    now = (now or utcnow()).astimezone(timezone.utc)
    with SessionLocal() as db:
        queued = schedule_due_backups(db, now)
        retried, failed = recover_stale_jobs(db, now)
        agent_changes = sync_agent_health(db, now)
        recovered = resolve_recovered_backup_issues(db, now)
        retained = apply_retention(db, now)
        db.commit()
        return {
            "queued": queued,
            "retried": retried,
            "failed": failed,
            "agent_changes": agent_changes,
            "recovered": recovered,
            "retention_removed": retained,
        }
