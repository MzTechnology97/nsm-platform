"""Cacti-style consolidation of agent telemetry (RRA).

With the 2-minute heartbeat every Agent stores a CPU/memory sample and one
traffic sample per monitored interface every 2 minutes.  Like an RRD archive,
full resolution is kept for 7 days; older samples are consolidated into one
10-minute point (average of CPU and bit/s, last counters and memory).  Over the
90-day retention this stores fewer rows than the former 5-minute heartbeat:
7 d × 720 + 83 d × 144 ≈ 17 000 points per series instead of 25 900.

The worker consolidates the band between 7 and 10 days ago every hour, so each
slot is processed once and older history is left untouched.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from app.db import SessionLocal
from app.models import utcnow

FULL_RESOLUTION = timedelta(days=7)
BAND = timedelta(days=3)
SLOT_SECONDS = 600

# (table, series columns, averaged columns, summed columns): the newest row of each
# slot is kept (latest counters, memory and uptime) and receives the averages of
# the slot; per-interval counts (errors, drops) are summed.
TABLES = (
    ("device_metric_samples", ("device_id",), ("cpu_load",), ()),
    ("device_interface_samples", ("device_id", "interface"), ("rx_bps", "tx_bps"), ("rx_errors", "tx_errors", "rx_drops", "tx_drops")),
    ("device_ping_samples", ("device_id",), ("rtt_min", "rtt_avg", "rtt_max"), ("sent", "received")),
)


def _statement(table: str, series: tuple, averaged: tuple, summed: tuple = ()):
    group = ", ".join(series)
    averages = ", ".join([f"avg({column}) AS {column}" for column in averaged] + [f"sum({column}) AS {column}" for column in summed])
    assignments = ", ".join(f"{column} = g.{column}" for column in (*averaged, *summed))
    return text(f"""
        WITH g AS (
            SELECT (array_agg(id ORDER BY observed_at DESC, id DESC))[1] AS keep, array_agg(id) AS ids, {averages}
            FROM {table}
            WHERE observed_at >= :start AND observed_at < :end
            GROUP BY {group}, floor(extract(epoch FROM observed_at) / {SLOT_SECONDS})
            HAVING count(*) > 1
        ), kept AS (
            UPDATE {table} s SET {assignments} FROM g WHERE s.id = g.keep RETURNING s.id
        )
        DELETE FROM {table} d USING g WHERE d.id = ANY(g.ids) AND d.id <> g.keep
    """)


def consolidate(now=None) -> int:
    """Consolidate the samples between 7 and 10 days old; returns the rows removed."""
    end = (now or utcnow()) - FULL_RESOLUTION
    start = end - BAND
    removed = 0
    with SessionLocal() as db:
        for table, series, averaged, summed in TABLES:
            removed += int(db.execute(_statement(table, series, averaged, summed), {"start": start, "end": end}).rowcount or 0)
        db.commit()
    return removed
