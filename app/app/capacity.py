"""Capacity and retention view for the *Sistema* page (production readiness).

Read-only: largest tables, backup archive size and growth with a days-to-full
estimate for the backup volume, and the retention applied to each data set.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, text

from app import login_security, mikrotik_telemetry, uisp_metrics
from app.backup_models import BackupArtifact
from app.models import utcnow

GROWTH_WINDOW = timedelta(days=30)
TABLE_LIMIT = 12


def table_sizes(db, limit: int = TABLE_LIMIT) -> list[dict]:
    try:
        rows = db.execute(text(
            "SELECT c.relname, pg_total_relation_size(c.oid), COALESCE(s.n_live_tup, 0) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 2 DESC LIMIT :limit"), {"limit": limit})
        return [{"name": name, "bytes": int(size or 0), "rows": int(estimate or 0)} for name, size, estimate in rows]
    except Exception:  # noqa: BLE001 - shown as unavailable
        db.rollback()
        return []


def backup_archive(db, free_bytes: int | None, now=None) -> dict:
    now = now or utcnow()
    live = BackupArtifact.deleted_at.is_(None)
    total, count = db.execute(select(func.coalesce(func.sum(BackupArtifact.size_bytes), 0), func.count(BackupArtifact.id)).where(live)).one()
    recent = db.scalar(select(func.coalesce(func.sum(BackupArtifact.size_bytes), 0)).where(live, BackupArtifact.created_at >= now - GROWTH_WINDOW))
    per_day = int(recent or 0) / GROWTH_WINDOW.days
    days_left = int(free_bytes / per_day) if free_bytes is not None and per_day > 0 else None
    return {"bytes": int(total or 0), "count": int(count or 0), "growth_30d": int(recent or 0), "days_left": days_left}


def retention() -> list[dict]:
    """Retention actually applied by the code, per data set."""
    return [
        {"data": "Telemetria MikroTik (CPU, RAM, traffico)", "rule": f"{mikrotik_telemetry.RETENTION_DAYS} giorni, pulizia oraria"},
        {"data": "Metriche UISP", "rule": f"{uisp_metrics.RETENTION_DAYS} giorni, pulizia oraria"},
        {"data": "Notifiche esterne inviate", "rule": "180 giorni (quelle fallite restano finché non vengono rimesse in coda)"},
        {"data": "Tentativi di accesso falliti", "rule": f"{int(login_security.RETENTION.total_seconds() // 86400)} giorno (gli eventi di audit restano)"},
        {"data": "File di backup degli apparati", "rule": "secondo la backup policy (giornalieri, settimanali, mensili)"},
        {"data": "Audit, job, snapshot, incidenti, report generati", "rule": "conservati senza scadenza (evidenza)"},
    ]
