"""Compliance figures shared by Device, Customer and report views (COMP-04)."""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select

from app.compliance_engine import CONTROLS
from app.compliance_findings import in_exception
from app.compliance_models import ComplianceResult
from app.models import Device


def summarize(db, now, *, customer_id=None, device_ids=None) -> dict:
    query = select(ComplianceResult, Device).join(Device, Device.id == ComplianceResult.device_id)
    if customer_id is not None:
        query = query.where(Device.customer_id == customer_id)
    if device_ids is not None:
        query = query.where(ComplianceResult.device_id.in_(list(device_ids) or [None]))
    counts: Counter = Counter()
    failing_controls: Counter = Counter()
    devices = set()
    failing_devices = set()
    per_device: dict = {}
    exceptions = []
    for result, device in db.execute(query):
        devices.add(device.id)
        state = "exception" if in_exception(result, now) else result.status
        counts[state] += 1
        bucket = per_device.setdefault(device.id, Counter())
        bucket[state] += 1
        if state == "fail":
            failing_devices.add(device.id)
            failing_controls[result.control_id] += 1
        if state == "exception":
            exceptions.append((result, device))
    return {
        "evaluated_devices": len(devices),
        "failing_devices": len(failing_devices),
        "counts": dict(counts),
        "failing_controls": [(CONTROLS[cid].title if cid in CONTROLS else cid, n) for cid, n in failing_controls.most_common()],
        "per_device": per_device,
        "exceptions": exceptions,
    }
