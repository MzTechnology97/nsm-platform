"""Operations pages for the new interface (phase 3).

- ``GET  /api/ui/v1/monitoring``: the fleet monitoring worklist (offline, Agent
  without heartbeat, high CPU, low memory, no ping reply, packet loss) with
  the same rows and thresholds as the classic page (``fleet_monitoring``);
- ``POST /api/ui/v1/monitoring/icmp``: switches the NSM ping on or off for the
  whole fleet or one customer (``devices.write``), same rule as the classic form.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import fleet_monitoring as fleet
from app import main as core
from app.db import SessionLocal
from app.models import Customer, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
MONITORING_LIMIT = 500
MONITORING_STATES = ("attention", "offline", "stale_agent", "high_cpu", "low_memory", "icmp_down", "packet_loss", "all")


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value or None


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def monitoring_row(row: dict) -> dict:
    device = row["device"]
    icmp = row["icmp"]
    return {
        "id": str(device.id), "name": device.display_name or device.device_identity or device.name,
        "vendor": device.vendor, "model": device.model, "status": device.status, "management_ip": device.management_ip,
        "last_seen": _iso(device.last_seen),
        "customer": {"id": str(device.customer.id), "name": device.customer.name} if device.customer else None,
        "site": device.site.name if device.site else None,
        "agent": row["agent"], "heartbeat": _iso(row["heartbeat"]),
        "cpu": row["cpu"], "memory_free_percent": row["memory_free_percent"], "uptime": row["uptime"], "sampled_at": _iso(row["sampled_at"]),
        "flags": list(row["flags"]),
        "icmp": {"enabled": icmp["enabled"], "target": icmp["effective_target"], "last_rtt": icmp["last_rtt"], "last_loss": icmp["last_loss"],
                 "down": icmp["down"], "last_at": icmp["last_at"]},
    }


@router.get("/monitoring")
def monitoring(request: Request, state: str = "attention", customer: str = "", vendor: str = ""):
    with SessionLocal() as db:
        user = ui_user(request, db, "monitoring.read")
        state = state if state in MONITORING_STATES else "attention"
        customer_id = _uuid(customer)
        rows, counts = fleet.fleet_rows(db, customer_id, vendor.strip(), utcnow())
        if state == "attention":
            rows = [row for row in rows if row["flags"]]
        elif state != "all":
            rows = [row for row in rows if state in row["flags"]]
        rows.sort(key=lambda row: (-len(row["flags"]), row["device"].status != "offline", -(row["cpu"] or 0)))
        return {
            "state": state, "total": len(rows), "limit": MONITORING_LIMIT,
            "items": [monitoring_row(row) for row in rows[:MONITORING_LIMIT]],
            "counts": counts,
            "thresholds": {"cpu": fleet.HIGH_CPU, "memory": fleet.LOW_MEMORY_PERCENT, "loss": fleet.HIGH_LOSS_PERCENT,
                           "stale_minutes": int(fleet.STALE_AGENT_AFTER.total_seconds() // 60)},
            "can_bulk": core.has_permission(user, "devices.write"),
        }


@router.post("/monitoring/icmp", dependencies=[Depends(require_csrf)])
def monitoring_icmp(request: Request, body: dict = Body(default={})):
    customer = (body or {}).get("customer") or ""
    enable = bool((body or {}).get("enable", True))
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        customer_id = _uuid(customer)
        if customer and (customer_id is None or db.get(Customer, customer_id) is None):
            raise HTTPException(404, {"code": "customer", "message": "Cliente non valido."})
        changed, skipped = fleet.set_icmp_bulk(db, user, customer_id, enable)
        db.commit()
        return {"enabled": enable, "changed": changed, "skipped": skipped}


def install_ui_operations(app) -> None:
    app.include_router(router)
