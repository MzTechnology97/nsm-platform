"""Support snapshot for legacy Agents (RouterOS 6.48/6.49 and 7.12).

The modern Agent builds the support snapshot in one JSON job, which legacy
RouterOS cannot serialize.  For a legacy Agent with structured snapshots
(0.49.3+) NSM splits the request into the read-only ``snapshot_section`` jobs
the Agent already runs (resources, addresses, routes, interfaces, PPP, DHCP,
logs).  From Agent 0.49.19 the legacy Agent runs up to 8 jobs per heartbeat,
so the whole snapshot arrives in one run; NSM assembles the sections into the
same result shape as the modern support snapshot.
"""
from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy import select

from app import main as core
from app import mikrotik_legacy_jobs as legacy_jobs
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.models import Device, utcnow

JOB = "support_snapshot"
SECTIONS = ("resources", "ip_addresses", "routes", "interfaces", "ppp_active", "dhcp_leases", "logs")
ROW_CAP = 50
OPEN = ("pending", "delivered", "running")


def supported(device) -> bool:
    data = device.inventory_data or {}
    return str(data.get("agent_transport") or "") == "legacy" and legacy_jobs.legacy_agent_supports_snapshots(device)


def split(db, device, job, now) -> list[DeviceJob]:
    """Turn a pending legacy support snapshot into its section jobs."""
    job.status = "running"
    job.delivered_at = now
    job.result = {"legacy_transport": True, "sections": list(SECTIONS)}
    children = []
    for section in SECTIONS:
        child = DeviceJob(device_id=device.id, job_type="snapshot_section", status="pending", expires_at=job.expires_at,
                          payload={"section": section, "support_parent": str(job.id)})
        db.add(child)
        children.append(child)
    db.flush()
    return children


def assemble(sections: dict) -> dict:
    """Modern support snapshot shape from the parsed legacy sections."""
    def rows(name):
        value = (sections.get(name) or {}).get("data")
        if name == "ppp_active" and isinstance(value, dict):
            value = value.get("active")
        return value if isinstance(value, list) else []

    data, total, truncated = {}, 0, False
    for name in ("ip_addresses", "routes", "interfaces", "ppp_active", "dhcp_leases"):
        items = rows(name)
        meta = sections.get(name) or {}
        total += int(meta.get("total") or len(items))
        truncated = truncated or bool(meta.get("truncated")) or len(items) > ROW_CAP
        data[name] = items[:ROW_CAP]
    logs = sections.get("logs") or {}
    data["resources"] = (sections.get("resources") or {}).get("data") or {}
    data["logs"] = rows("logs")
    data["logs_meta"] = {"total": logs.get("total", len(data["logs"])), "limit": logs.get("limit", 20), "truncated": bool(logs.get("truncated"))}
    data["rows_meta"] = {"total": total, "limit": ROW_CAP, "truncated": truncated}
    return data


def finish_parent(db, device, parent_id, now=None) -> None:
    """Close the support snapshot once every section job is closed (or the snapshot expired)."""
    parent = db.get(DeviceJob, uuid.UUID(str(parent_id)))
    if parent is None or parent.job_type != JOB or parent.status != "running":
        return
    now = now or utcnow()
    expired = parent.expires_at is not None and parent.expires_at <= now
    children = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device.id, DeviceJob.job_type == "snapshot_section")))
    children = [c for c in children if (c.payload or {}).get("support_parent") == str(parent.id)]
    if not expired and any(c.status in OPEN for c in children):
        return
    done = {(c.payload or {}).get("section"): c.result for c in children if c.status == "success" and isinstance(c.result, dict)}
    missing = [s for s in SECTIONS if s not in done]
    parent.completed_at = now
    if not done:
        parent.status = "failed"
        parent.last_error = "Nessuna sezione del support snapshot è stata raccolta dall'agent legacy."
    else:
        parent.status = "success"
        parent.last_error = None
        parent.result = {"data": assemble(done), "legacy_transport": True, "missing_sections": missing}
    core.add_event(db, "DEVICE_JOB_COMPLETED", customer_id=device.customer_id, device_id=device.id, severity="warning" if missing else "info",
                   result=parent.status, source="mikrotik_agent_legacy",
                   details={"job_id": str(parent.id), "job_type": JOB, "status": parent.status, "transport": "routeros_legacy", "missing_sections": missing})


def _install_delivery() -> None:
    previous = legacy_jobs._fail_deferred_jobs

    def fail_deferred(db, device, now):
        if supported(device):
            for job in db.scalars(select(DeviceJob).where(DeviceJob.device_id == device.id, DeviceJob.job_type == JOB, DeviceJob.status == "pending")):
                if job.expires_at is None or job.expires_at > now:
                    split(db, device, job, now)
            for job in db.scalars(select(DeviceJob).where(DeviceJob.device_id == device.id, DeviceJob.job_type == JOB, DeviceJob.status == "running")):
                finish_parent(db, device, job.id, now)
            db.flush()
        return previous(db, device, now)

    legacy_jobs._fail_deferred_jobs = fail_deferred


def _install_completion() -> None:
    previous = legacy_jobs.legacy_job_complete

    async def complete(request: Request, job_id: uuid.UUID, status: str = "failed"):
        response = await previous(request, job_id, status)
        with SessionLocal() as db:
            job = db.get(DeviceJob, job_id)
            parent = (job.payload or {}).get("support_parent") if job else None
            if parent:
                finish_parent(db, db.get(Device, job.device_id), parent)
                db.commit()
        return response

    legacy_jobs.legacy_job_complete = complete


def install_mikrotik_legacy_support() -> None:
    _install_delivery()
    _install_completion()
