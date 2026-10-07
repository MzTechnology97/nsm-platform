"""Telemetry retention that never forgets the last observation (MON-02).

Samples older than the retention window are deleted, except the newest one of
each series (device, or device + interface): a device that went offline months
ago still shows the last telemetry it sent.
"""
from __future__ import annotations

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import aliased


def expire_keep_latest(db, model, cutoff, *series) -> int:
    """Delete ``model`` rows older than ``cutoff`` that are not the newest of their series."""
    newer = aliased(model)
    same_series = [getattr(newer, column) == getattr(model, column) for column in series]
    latest = select(func.max(newer.observed_at)).where(and_(*same_series)).scalar_subquery()
    result = db.execute(
        delete(model)
        .where(model.observed_at < cutoff, model.observed_at < latest)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)
