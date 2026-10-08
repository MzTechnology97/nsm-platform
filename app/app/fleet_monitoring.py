"""Fleet monitoring worklist: reachability, Agent freshness and latest telemetry.

Monitoring stays lightweight (NSM is not a full NMS): it surfaces the Devices
that need attention now, using data NSM already collects — Device status and
last-seen, MikroTik Agent heartbeat freshness, the latest telemetry sample and
the ICMP monitor (round-trip time and loss measured from NSM), which can be
switched on for a whole customer from this page.
"""
from __future__ import annotations

import math
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app import icmp_monitor
from app import main as core
from app.agent_models import DeviceAgentCredential, DeviceMetricSample
from app.db import SessionLocal
from app.models import Customer, Device, utcnow
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
STALE_AGENT_AFTER = timedelta(minutes=15)
SAMPLE_MAX_AGE = timedelta(hours=2)
HIGH_CPU = 80.0
LOW_MEMORY_PERCENT = 10.0
HIGH_LOSS_PERCENT = 20
PAGE_SIZE = 50
STATES = {
    "attention": "Da verificare",
    "offline": "Offline",
    "stale_agent": "Agent senza heartbeat",
    "high_cpu": f"CPU ≥ {int(HIGH_CPU)}%",
    "low_memory": f"Memoria libera < {int(LOW_MEMORY_PERCENT)}%",
    "icmp_down": "Non risponde al ping",
    "packet_loss": f"Perdita ≥ {HIGH_LOSS_PERCENT}%",
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
    icmp = icmp_monitor.panel(device)
    if icmp["enabled"] and icmp["down"]:
        flags.append("icmp_down")
    elif icmp["enabled"] and icmp["last_loss"] is not None and icmp["last_loss"] >= HIGH_LOSS_PERCENT:
        flags.append("packet_loss")
    return {
        "device": device,
        "heartbeat": heartbeat,
        "agent": credential is not None,
        "cpu": round(sample.cpu_load, 1) if sample and sample.cpu_load is not None else None,
        "memory_free_percent": memory_percent,
        "uptime": sample.uptime_text if sample else None,
        "sampled_at": sample.observed_at if sample else None,
        "flags": flags,
        "icmp": icmp,
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

        counts = {key: sum(1 for row in rows if key in row["flags"]) for key in ("offline", "stale_agent", "high_cpu", "low_memory", "icmp_down", "packet_loss")}
        counts["icmp_enabled"] = sum(1 for row in rows if row["icmp"]["enabled"])
        counts["icmp_ready"] = sum(1 for row in rows if not row["icmp"]["enabled"] and row["icmp"]["effective_target"])
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


@router.post("/operations/monitoring/icmp", name="fleet_monitoring_icmp")
async def icmp_bulk(request: Request):
    """Switch the ICMP monitor on (or off) for every device with an address, optionally of one customer."""
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    enable = form.get("action") != "disable"
    customer = str(form.get("customer") or "")
    back = "/operations/monitoring?state=all" + (f"&customer={customer}" if customer else "")
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        stmt = select(Device)
        customer_id = None
        if customer:
            try:
                customer_id = uuid.UUID(customer)
            except ValueError:
                return flash_redirect(request, "/operations/monitoring", "warning", "Cliente non valido.", title="Monitoraggio ICMP")
            stmt = stmt.where(Device.customer_id == customer_id)
        changed = skipped = 0
        for device in db.scalars(stmt):
            conf = icmp_monitor.settings(device)
            if enable and not icmp_monitor.target_for(device):
                skipped += 1
                continue
            if bool(conf.get("enabled")) == enable:
                continue
            data = dict(device.inventory_data or {})
            conf["enabled"] = enable
            if not enable:
                conf["consecutive_losses"] = 0
            data[icmp_monitor.SETTINGS_KEY] = conf
            device.inventory_data = data
            changed += 1
        core.add_event(db, "ICMP_MONITOR_BULK", actor=user, source="portal", customer_id=customer_id,
                       details={"enabled": enable, "changed": changed, "skipped": skipped})
        db.commit()
    if enable:
        message = f"Monitoraggio ICMP attivato su {changed} apparati."
        if skipped:
            message += f" {skipped} senza IP di gestione: indicalo nella pagina dell'apparato."
    else:
        message = f"Monitoraggio ICMP disattivato su {changed} apparati."
    return flash_redirect(request, back, "success", message, title="Monitoraggio ICMP")


def install_fleet_monitoring(app) -> None:
    app.include_router(router)
