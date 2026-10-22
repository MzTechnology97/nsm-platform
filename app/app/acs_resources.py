"""CPU, memory and uptime of TR-069 CPEs, read from GenieACS (ACS-05, first step).

Every ``INTERVAL`` the worker reads, with one NBI query for all linked CPEs,
the values GenieACS last obtained from them (TR-181
``Device.DeviceInfo.ProcessStatus.CPUUsage`` / ``MemoryStatus.Total|Free`` /
``UpTime``, the same names under ``InternetGatewayDevice.DeviceInfo`` for
TR-098). Nothing is sent to the CPEs: the NSM provision (``acs_admin``)
makes GenieACS re-read these values at every periodic inform (5 minutes).

The result is kept in ``inventory_data["genieacs_metrics"]`` with the time the
CPE reported it; values older than ``MAX_AGE`` are not shown as current.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from app import genieacs_connector as acs
from app.db import SessionLocal
from app.models import Device, utcnow

INTERVAL = timedelta(minutes=5)
MAX_AGE = timedelta(hours=2)
BATCH = 200
ROOTS = ("Device.DeviceInfo", "InternetGatewayDevice.DeviceInfo")
FIELDS = ("ProcessStatus.CPUUsage", "MemoryStatus.Total", "MemoryStatus.Free", "UpTime")
TRANSPORT = None  # tests swap in an httpx.MockTransport
_last_run: datetime | None = None


def _leaf(record: dict, path: str):
    node = record
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node if isinstance(node, dict) and "_value" in node else None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def metrics_from(record: dict) -> dict | None:
    """CPU %, RAM % and uptime from one GenieACS record, with the oldest read time among them; None when the CPE exposes none."""
    for root in ROOTS:
        leaves = {field: _leaf(record, f"{root}.{field}") for field in FIELDS}
        if not any(leaves.values()):
            continue
        value = {field: _number(leaf.get("_value")) if leaf else None for field, leaf in leaves.items()}
        cpu = value["ProcessStatus.CPUUsage"]
        total, free = value["MemoryStatus.Total"], value["MemoryStatus.Free"]
        ram = round(max(0.0, min(100.0, (1 - free / total) * 100)), 1) if total and free is not None else None
        stamps = [str(leaf.get("_timestamp")) for leaf in leaves.values() if leaf and leaf.get("_timestamp")]
        return {"cpu_percent": cpu if cpu is not None and 0 <= cpu <= 100 else None, "ram_percent": ram,
                "uptime_seconds": int(value["UpTime"]) if value["UpTime"] is not None and value["UpTime"] >= 0 else None,
                "memory_total_kib": int(total) if total else None, "read_at": min(stamps) if stamps else None}
    return None


def current(device) -> dict | None:
    """The stored metrics when recent enough to be shown as current."""
    metrics = (device.inventory_data or {}).get("genieacs_metrics") or {}
    read_at = metrics.get("read_at")
    try:
        when = datetime.fromisoformat(str(read_at).replace("Z", "+00:00")) if read_at else None
    except ValueError:
        when = None
    return metrics if when and utcnow() - when <= MAX_AGE else None


def refresh(db, now: datetime | None = None) -> dict:
    """Read the metrics of every linked TR-069 CPE from GenieACS and store them; returns counters."""
    connection = acs._connection(db)
    if connection is None or not connection.is_enabled:
        return {"status": "disabled"}
    devices = {d.external_device_id: d for d in db.scalars(select(Device).where(Device.external_device_id.is_not(None)))
               if acs.tr069_capable(d)}
    stats = {"status": "success", "cpes": len(devices), "with_metrics": 0, "changed": 0}
    if not devices:
        return stats
    auth = acs._load_auth(connection)
    headers, client_auth = {"Accept": "application/json"}, None
    if auth["mode"] == "basic":
        client_auth = httpx.BasicAuth(auth["username"], auth["secret"])
    elif auth["mode"] == "bearer":
        headers["Authorization"] = f"Bearer {auth['secret']}"
    projection = ",".join(f"{root}.{field}" for root in ROOTS for field in FIELDS)
    ids = sorted(devices)
    with httpx.Client(base_url=connection.base_url.rstrip("/"), timeout=acs.GENIEACS_TIMEOUT_SECONDS, verify=bool(connection.verify_tls),
                      follow_redirects=False, headers=headers, auth=client_auth, transport=TRANSPORT) as client:
        for start in range(0, len(ids), BATCH):
            response = client.get("/devices", params={"query": json.dumps({"_id": {"$in": ids[start:start + BATCH]}}), "projection": projection})
            if response.status_code != 200:
                return {**stats, "status": "failed", "error": f"HTTP {response.status_code}"}
            for record in response.json() or []:
                device = devices.get(record.get("_id"))
                metrics = metrics_from(record) if device else None
                if not metrics:
                    continue
                stats["with_metrics"] += 1
                data = dict(device.inventory_data or {})
                if data.get("genieacs_metrics") != metrics:
                    data["genieacs_metrics"] = metrics
                    device.inventory_data = data
                    stats["changed"] += 1
    db.commit()
    return stats


def scheduled_refresh(now: datetime | None = None) -> dict:
    global _last_run
    now = now or utcnow()
    if _last_run is not None and now - _last_run < INTERVAL:
        return {"status": "not_due"}
    _last_run = now
    with SessionLocal() as db:
        try:
            return refresh(db, now)
        except (httpx.HTTPError, acs.GenieAcsConnectorError) as exc:
            return {"status": "failed", "error": type(exc).__name__}
