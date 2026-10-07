"""Fleet monitoring worklist: reachability, Agent freshness and latest telemetry.

Monitoring stays lightweight (NSM is not a full NMS): it surfaces the Devices
that need attention now, using data NSM already collects — Device status and
last-seen, MikroTik Agent heartbeat freshness and the latest telemetry sample.
"""
from __future__ import annotations

import math
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.agent_models import DeviceAgentCredential, DeviceMetricSample
from app.db import SessionLocal
from app.models import Customer, Device, utcnow

router = APIRouter()
STALE_AGENT_AFTER = timedelta(minutes=15)
SAMPLE_MAX_AGE = timedelta(hours=2)
HIGH_CPU = 80.0
LOW_MEMORY_PERCENT = 10.0
PAGE_SIZE = 50
STATES = {
    "attention": "Da verificare",
    "offline": "Offline",
    "stale_agent": "Agent senza heartbeat",
    "high_cpu": f"CPU ≥ {int(HIGH_CPU)}%",
    "low_memory": f"Memoria libera < {int(LOW_MEMORY_PERCENT)}%",
    "all": "Tutti",
}


def _latest_samples(db, device_ids, since):
    if not device_ids:
        return {}
    latest = (
        select(DeviceMetricSample.device_id, func.max(DeviceMetricSample.observed_at).label("observed_at"))
        .where(DeviceMetricSample.device_id.in_(device_ids), DeviceMetricSample.observed_at >= since)
        .group_by(DeviceMetricSample.device_id)
        .subquery()
    )
    rows = db.scalars(
        select(DeviceMetricSample).join(
            latest,
            (latest.c.device_id == DeviceMetricSample.device_id) & (latest.c.observed_at == DeviceMetricSample.observed_at),
        )
    )
    return {sample.device_id: sample for sample in rows}


def _row(device, credential, sample, now):
    memory_percent = None
    if sample and sample.free_memory_bytes is not None and sample.total_memory_bytes:
        memory_percent = round(100.0 * sample.free_memory_bytes / sample.total_memory_bytes, 1)
    heartbeat = credential.last_used_at if credential else None
    flags = []
    if device.status == "offline":
        flags.append("offline")
    if credential and (heartbeat is None or heartbeat < now - STALE_AGENT_AFTER):
        flags.append("stale_agent")
    if sample and sample.cpu_load is not None and sample.cpu_load >= HIGH_CPU:
        flags.append("high_cpu")
    if memory_percent is not None and memory_percent < LOW_MEMORY_PERCENT:
        flags.append("low_memory")
    return {
        "device": device,
        "heartbeat": heartbeat,
        "agent": credential is not None,
        "cpu": round(sample.cpu_load, 1) if sample and sample.cpu_load is not None else None,
        "memory_free_percent": memory_percent,
        "uptime": sample.uptime_text if sample else None,
        "sampled_at": sample.observed_at if sample else None,
        "flags": flags,
    }


@router.get("/operations/monitoring", response_class=HTMLResponse, name="fleet_monitoring")
def fleet_monitoring(request: Request, state: str = "attention", customer: str = "", vendor: str = "", page: int = 1):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "monitoring.read"):
            raise HTTPException(403)
        state = state if state in STATES else "attention"
        now = utcnow()
        stmt = select(Device).options(selectinload(Device.customer), selectinload(Device.site))
        customer_id = None
        if customer:
            try:
                customer_id = uuid.UUID(customer)
                stmt = stmt.where(Device.customer_id == customer_id)
            except ValueError:
                customer_id = None
        if vendor:
            stmt = stmt.where(Device.vendor == vendor)
        devices = list(db.scalars(stmt.order_by(Device.name)))
        device_ids = [device.id for device in devices]
        credentials = {
            credential.device_id: credential
            for credential in db.scalars(
                select(DeviceAgentCredential).where(
                    DeviceAgentCredential.device_id.in_(device_ids) if device_ids else DeviceAgentCredential.id.is_(None),
                    DeviceAgentCredential.agent_type == "mikrotik_agent",
                    DeviceAgentCredential.is_active.is_(True),
                )
            )
        }
        samples = _latest_samples(db, device_ids, now - SAMPLE_MAX_AGE)
        rows = [_row(device, credentials.get(device.id), samples.get(device.id), now) for device in devices]

        counts = {key: sum(1 for row in rows if key in row["flags"]) for key in ("offline", "stale_agent", "high_cpu", "low_memory")}
        counts["attention"] = sum(1 for row in rows if row["flags"])
        counts["online"] = sum(1 for row in rows if row["device"].status == "online")
        counts["total"] = len(rows)
        counts["sampled"] = sum(1 for row in rows if row["sampled_at"])

        if state == "attention":
            rows = [row for row in rows if row["flags"]]
        elif state != "all":
            rows = [row for row in rows if state in row["flags"]]
        rows.sort(key=lambda row: (-len(row["flags"]), row["device"].status != "offline", -(row["cpu"] or 0)))

        total = len(rows)
        pages = max(1, math.ceil(total / PAGE_SIZE))
        page = min(max(1, page), pages)
        rows = rows[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        return core.render(
            request,
            db,
            user,
            "fleet_monitoring.html",
            rows=rows,
            counts=counts,
            states=STATES,
            state=state,
            customer_filter=str(customer_id) if customer_id else "",
            vendor_filter=vendor,
            customers=list(db.scalars(select(Customer).where(Customer.is_active.is_(True)).order_by(Customer.name))),
            vendors=sorted({v for v in db.scalars(select(Device.vendor).distinct()) if v}),
            page=page,
            pages=pages,
            total=total,
            thresholds={"cpu": HIGH_CPU, "memory": LOW_MEMORY_PERCENT, "stale_minutes": int(STALE_AGENT_AFTER.total_seconds() // 60)},
        )


def install_fleet_monitoring(app) -> None:
    app.include_router(router)
