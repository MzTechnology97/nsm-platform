"""Action Center issue for interfaces with errors (MON-01).

From Agent 0.49.20 NSM stores, for every monitored interface, the receive and
transmit errors since the previous heartbeat.  Every 10 minutes the worker sums
the last hour: an interface with at least ``INTERFACE_ERROR_ALERT_PER_HOUR``
errors (default 50) opens *Errori sull'interfaccia* for its device, with a
notification; the issue lists every interface over the threshold and closes
when they are all below it.  Drops are shown on the graph but do not alert:
queues and shaping drop packets on healthy links.  ``0`` disables the alert.
"""
from __future__ import annotations

import os
import time
from datetime import timedelta

from sqlalchemy import func, select

from app import main as core
from app.agent_models import DeviceInterfaceSample
from app.db import SessionLocal
from app.models import ActionIssue, Device, Notification, utcnow

CATEGORY = "interface_errors"
TITLE = "Errori sull'interfaccia"
WINDOW = timedelta(hours=1)
EVERY_SECONDS = 600
_LAST = {"at": 0.0}


def threshold() -> int:
    try:
        return max(0, int(os.getenv("INTERFACE_ERROR_ALERT_PER_HOUR", "50")))
    except ValueError:
        return 50


def errors_last_hour(db, now) -> dict:
    """{device_id: {interface: (rx_errors, tx_errors)}} over the window, monitored interfaces only."""
    rows = db.execute(select(DeviceInterfaceSample.device_id, DeviceInterfaceSample.interface,
                             func.sum(DeviceInterfaceSample.rx_errors), func.sum(DeviceInterfaceSample.tx_errors))
                      .where(DeviceInterfaceSample.observed_at >= now - WINDOW, DeviceInterfaceSample.rx_errors.is_not(None))
                      .group_by(DeviceInterfaceSample.device_id, DeviceInterfaceSample.interface)).all()
    result: dict = {}
    for device_id, interface, rx, tx in rows:
        result.setdefault(device_id, {})[interface] = (int(rx or 0), int(tx or 0))
    return result


def evaluate(now=None, force=False) -> dict:
    stats = {"opened": 0, "resolved": 0}
    limit = threshold()
    if not limit:
        return stats
    if not force and time.monotonic() - _LAST["at"] < EVERY_SECONDS:
        return stats
    _LAST["at"] = time.monotonic()
    now = now or utcnow()
    with SessionLocal() as db:
        counts = errors_last_hour(db, now)
        open_issues = {issue.device_id: issue for issue in db.scalars(select(ActionIssue).where(
            ActionIssue.category == CATEGORY, ActionIssue.status.in_(["open", "acknowledged"])))}
        for device_id in set(counts) | set(open_issues):
            over = {name: {"rx_errors": rx, "tx_errors": tx} for name, (rx, tx) in (counts.get(device_id) or {}).items() if rx + tx >= limit}
            issue = open_issues.get(device_id)
            device = db.get(Device, device_id)
            if device is None:
                continue
            if not over:
                if issue:
                    issue.status = "resolved"
                    issue.details = {**(issue.details or {}), "resolved_reason": f"meno di {limit} errori nell'ultima ora su tutte le interfacce"}
                    stats["resolved"] += 1
                continue
            details = {"interfaces": over, "threshold_per_hour": limit, "checked_at": now.isoformat()}
            if issue:
                issue.details = details
                issue.updated_at = now
                continue
            name = device.display_name or device.device_identity or device.name
            summary = ", ".join(f"{iface} ({v['rx_errors']} rx / {v['tx_errors']} tx)" for iface, v in sorted(over.items()))
            db.add(ActionIssue(category=CATEGORY, severity="warning", status="open", title=TITLE, details=details,
                               customer_id=device.customer_id, device_id=device.id))
            db.add(Notification(severity="medium", category="monitoring", title=f"{TITLE}: {name}",
                                message=f"{name}: errori nell'ultima ora su {summary}. Controlla cavo, porta, duplex o segnale radio.",
                                customer_id=device.customer_id, device_id=device.id, source_url=f"/devices/{device.id}/monitor#traffic", is_active=True))
            core.add_event(db, "INTERFACE_ERRORS_DETECTED", customer_id=device.customer_id, device_id=device.id, details=details,
                           severity="warning", source="worker")
            stats["opened"] += 1
        db.commit()
    return stats
