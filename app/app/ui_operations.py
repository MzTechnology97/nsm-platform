"""Operations pages for the new interface (phase 3).

- ``GET  /api/ui/v1/monitoring``: the fleet monitoring worklist (offline, Agent
  without heartbeat, high CPU, low memory, no ping reply, packet loss) with
  the same rows and thresholds as the classic page (``fleet_monitoring``);
- ``POST /api/ui/v1/monitoring/icmp``: switches the NSM ping on or off for the
  whole fleet or one customer (``devices.write``), same rule as the classic form.
- ``GET  /api/ui/v1/backups``: backup protection of the fleet (protected,
  blocked, without policy, last run failed or never run) per device and per
  customer, plus the policies, from ``backup_workspace_capabilities``;
- ``GET  /api/ui/v1/agents``: the MikroTik Agent fleet (health, installation
  check, transport, version and pending update) from ``agent_fleet``, including
  the install-time extensions of its row builder.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import agent_fleet
from app import backup_workspace_capabilities as backup_overview
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


AGENT_LIMIT = 500


def agent_row(row: dict) -> dict:
    device = row["device"]
    update = row.get("agent_update") or {}
    return {
        "id": str(device.id), "name": device.display_name or device.device_identity or device.name, "model": device.model,
        "firmware": device.firmware_version, "status": device.status, "management_ip": device.management_ip,
        "customer": {"id": str(device.customer.id), "name": device.customer.name} if device.customer else None,
        "site": device.site.name if device.site else None,
        "transport": row["transport"], "agent_version": row["agent_version"], "heartbeat_transport": row["heartbeat_transport"],
        "source_ip": row["source_ip"], "last_seen": _iso(row["last_seen"]), "stale": bool(row["stale"]),
        "credential_active": bool(row["credential_active"]), "pending_enrollments": row["pending_enrollments"],
        "health": row["health"], "attention": bool(row["attention"]), "install": row["install_bucket"],
        "installation": row["installation"]["key"], "reasons": list(row["reasons"]),
        "update": {"outdated": bool(update.get("outdated")), "target_version": update.get("target_version"),
                   "state": update.get("state") or "idle", "self_update_capable": bool(update.get("self_update_capable"))} if update else None,
    }


@router.get("/agents")
def agents(request: Request, q: str = "", customer: str = "", state: str = "attention", transport: str = "", install: str = ""):
    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        state = state if state in agent_fleet.STATES else "attention"
        transport = transport if transport in ("legacy", "modern", "unknown") else ""
        install = install if install in agent_fleet.INSTALL_FILTERS else ""
        scope_rows, summary = agent_fleet.fleet_scope(db, q.strip()[:100], _uuid(customer))
        rows = agent_fleet.filter_rows(scope_rows, state, transport, install)
        # Only installed agents can be updated: a device still waiting for its first pairing is not "outdated".
        summary["outdated"] = sum(1 for row in scope_rows if row["credential_active"] and (row.get("agent_update") or {}).get("outdated"))
        summary.update({key: sum(1 for row in scope_rows if agent_fleet._matches_health(row, key))
                        for key in ("offline", "stale", "no_credential", "unknown_transport")})
        return {"state": state, "total": len(rows), "limit": AGENT_LIMIT, "summary": summary,
                "items": [agent_row(row) for row in rows[:AGENT_LIMIT]]}


BACKUP_LIMIT = 500


def backup_row(row: dict) -> dict:
    device = row["device"]
    latest = row["latest"]
    readiness = row["readiness"]
    return {
        "id": str(device.id), "name": device.display_name or device.device_identity or device.name, "vendor": device.vendor, "status": device.status,
        "customer": {"id": str(device.customer.id), "name": device.customer.name} if device.customer else None,
        "site": device.site.name if device.site else None,
        "policy": {"id": str(row["policy"].id), "name": row["policy"].name} if row["policy"] else None,
        "readiness": {"status": readiness.status, "label": row["readiness_label"], "reason": readiness.reason, "method": readiness.capability.label},
        "bucket": row["bucket"], "state": row["state"],
        "latest": {"status": latest.status, "started_at": _iso(latest.started_at), "completed_at": _iso(latest.completed_at),
                   "error": latest.error_message, "type": latest.backup_type} if latest else None,
    }


@router.get("/backups")
def backups(request: Request, view: str = "attention", customer: str = "", q: str = ""):
    with SessionLocal() as db:
        user = ui_user(request, db, "backup.read")
        view = view if view in backup_overview.BACKUP_VIEWS else "attention"
        state = backup_overview.backup_overview_state(db)
        counts, rows = backup_overview.select_backup_rows(state["device_rows"], view, _uuid(customer), q[:100])
        customers = state["customer_rows"]
        return {
            "view": view, "total": len(rows), "limit": BACKUP_LIMIT,
            "counts": {key: counts.get(key, 0) for key in backup_overview.BACKUP_VIEWS},
            "totals": {"devices": len(state["devices"]), "protected": sum(r["protected"] for r in customers), "blocked": sum(r["blocked"] for r in customers),
                       "no_policy": sum(r["no_policy"] for r in customers), "backup_alerts": sum(r["backup_alerts"] for r in customers),
                       "active_policies": sum(1 for p in state["policies"] if p.is_enabled)},
            "items": [backup_row(row) for row in rows[:BACKUP_LIMIT]],
            "customers": [{"id": str(r["customer"].id), "name": r["customer"].name, "devices": r["devices"], "protected": r["protected"],
                           "blocked": r["blocked"], "no_policy": r["no_policy"], "backup_alerts": r["backup_alerts"]}
                          for r in customers if r["devices"]],
            "policies": [{"id": str(r["policy"].id), "name": r["policy"].name, "enabled": bool(r["policy"].is_enabled), "scope": r["scope"],
                          "scope_type": r["policy"].scope_type, "schedule": r["policy"].schedule_cron} for r in state["policy_rows"]],
            "can_configure": core.has_permission(user, "backup.configure"),
        }


def install_ui_operations(app) -> None:
    app.include_router(router)
