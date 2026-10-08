"""Tamper-evident storage of warning/error syslog lines (LOG-01 hardening).

Every line with severity warning or worse gets ``chain_hash`` =
sha256(previous hash of the same device + canonical line).  Changing,
inserting or deleting a chained line breaks the chain from that point, and
``verify`` reports where.  Once a day the worker writes the head of every
chain into the audit log (``SYSLOG_CHAIN_ANCHOR``): rewriting a whole chain
would also require rewriting the audit trail.

These lines are kept for the whole life of the device, so retention never
breaks a chain; info/notice/debug lines are not chained.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta, timezone

from sqlalchemy import func, select

from app.models import AuditEvent, utcnow
from app.syslog_models import DeviceLogEntry

CHAINED_SEVERITY = 4
ANCHOR_EVENT = "SYSLOG_CHAIN_ANCHOR"


def canonical(entry) -> str:
    get = entry.get if isinstance(entry, dict) else lambda k: getattr(entry, k)
    received = get("received_at")
    # UTC, so the value read back from PostgreSQL (session time zone) hashes the same.
    stamp = received.astimezone(timezone.utc).isoformat() if received else ""
    return "|".join([stamp, str(get("source_ip") or ""), str(get("severity")),
                     str(get("topics") or ""), str(get("program") or ""), str(get("hostname") or ""), str(get("message") or "")])


def link(previous: str | None, device_id, entry) -> str:
    seed = previous or f"GENESIS:{device_id}"
    return hashlib.sha256(f"{seed}\n{canonical(entry)}".encode("utf-8")).hexdigest()


def chained(entry) -> bool:
    severity = entry.get("severity") if isinstance(entry, dict) else entry.severity
    return severity is not None and int(severity) <= CHAINED_SEVERITY


def head(db, device_id) -> str | None:
    return db.scalar(select(DeviceLogEntry.chain_hash).where(DeviceLogEntry.device_id == device_id, DeviceLogEntry.chain_hash.is_not(None))
                     .order_by(DeviceLogEntry.id.desc()).limit(1))


def verify(db, device_id, batch: int = 5000) -> dict:
    """Recompute the chain of one device; ``ok`` False with the first broken line id."""
    previous, checked, last_id = None, 0, 0
    while True:
        rows = list(db.scalars(select(DeviceLogEntry).where(DeviceLogEntry.device_id == device_id, DeviceLogEntry.id > last_id,
                                                            DeviceLogEntry.severity <= CHAINED_SEVERITY)
                               .order_by(DeviceLogEntry.id).limit(batch)))
        if not rows:
            break
        for row in rows:
            last_id = row.id
            if row.chain_hash is None:
                continue  # stored before the chain existed
            expected = link(previous, device_id, row)
            if row.chain_hash != expected:
                return {"ok": False, "checked": checked, "broken_at": row.id, "received_at": row.received_at, "head": previous}
            previous = row.chain_hash
            checked += 1
    anchor = db.scalar(select(AuditEvent).where(AuditEvent.event_type == ANCHOR_EVENT, AuditEvent.device_id == device_id)
                       .order_by(AuditEvent.timestamp.desc()).limit(1))
    anchored = None
    if anchor is not None:
        anchored_head = (anchor.details or {}).get("head")
        anchored = anchored_head in _chain_hashes(db, device_id, anchored_head)
    return {"ok": anchored is not False, "checked": checked, "broken_at": None, "head": previous,
            "anchored_at": anchor.timestamp if anchor is not None else None, "anchor_found": anchored}


def _chain_hashes(db, device_id, value) -> set:
    if not value:
        return set()
    return set(db.scalars(select(DeviceLogEntry.chain_hash).where(DeviceLogEntry.device_id == device_id, DeviceLogEntry.chain_hash == value)))


def anchor_heads(now=None) -> dict:
    """Worker, once a day: write each chain head into the audit log."""
    from app import main as core
    from app.db import SessionLocal

    now = now or utcnow()
    stats = {"anchored": 0}
    with SessionLocal() as db:
        last = db.scalar(select(func.max(AuditEvent.timestamp)).where(AuditEvent.event_type == ANCHOR_EVENT))
        if last is not None and now - last < timedelta(hours=23):
            return stats
        device_ids = list(db.scalars(select(DeviceLogEntry.device_id).where(DeviceLogEntry.chain_hash.is_not(None)).distinct()))
        for device_id in device_ids:
            value = head(db, device_id)
            count = db.scalar(select(func.count()).select_from(DeviceLogEntry).where(DeviceLogEntry.device_id == device_id,
                                                                                      DeviceLogEntry.chain_hash.is_not(None))) or 0
            core.add_event(db, ANCHOR_EVENT, device_id=device_id, details={"head": value, "lines": count}, source="worker")
            stats["anchored"] += 1
        db.commit()
    return stats
