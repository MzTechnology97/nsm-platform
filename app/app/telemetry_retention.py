"""Telemetry retention that never forgets the last observation (MON-02).

Samples older than the retention window are deleted, except the newest one of
each series (device, or device + interface): a device that went offline months
ago still shows the last telemetry it sent.
"""
from __future__ import annotations

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import aliased


def expire_keep_latest(db, model, cutoff, *series, also_keep: tuple[str, ...] = ()) -> int:
    """Delete ``model`` rows older than ``cutoff`` that are not the newest of their series.

    ``also_keep`` names columns whose newest non-empty value is kept too (e.g. an optical level read less
    often than the other values).
    """
    newer = aliased(model)
    same_series = [getattr(newer, column) == getattr(model, column) for column in series]
    latest = select(func.max(newer.observed_at)).where(and_(*same_series)).scalar_subquery()
    conditions = [model.observed_at < cutoff, model.observed_at < latest]
    for column in also_keep:
        latest_value = (select(func.max(newer.observed_at)).where(and_(*same_series), getattr(newer, column).is_not(None))
                        .scalar_subquery())
        conditions.append(or_(getattr(model, column).is_(None), model.observed_at < latest_value))
    result = db.execute(
        delete(model)
        .where(*conditions)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)
