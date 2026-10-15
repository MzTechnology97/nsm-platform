"""Fleet monitoring worklist: reachability, Agent freshness and latest telemetry.

Monitoring stays lightweight (NSM is not a full NMS): it surfaces the Devices
that need attention now, using data NSM already collects — Device status and
last-seen, MikroTik Agent heartbeat freshness, the latest telemetry sample and
the ICMP monitor (round-trip time and loss measured from NSM), which can be
switched on for a whole customer from this page.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app import icmp_monitor
from app import main as core
from app.agent_models import DeviceAgentCredential, DeviceMetricSample
from app.models import Device, utcnow

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


def fleet_rows(db, customer_id=None, vendor: str = "", now=None):
    """Monitoring rows (device, agent heartbeat, latest telemetry, ICMP, flags) and the state counters."""
    now = now or utcnow()
    stmt = select(Device).options(selectinload(Device.customer), selectinload(Device.site))
    if customer_id:
        stmt = stmt.where(Device.customer_id == customer_id)
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
    return rows, counts


def set_icmp_bulk(db, user, customer_id, enable: bool) -> tuple[int, int]:
    """Switch the ICMP monitor on/off for every device (of one customer); devices without an address are skipped when enabling."""
    stmt = select(Device)
    if customer_id:
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
    return changed, skipped


