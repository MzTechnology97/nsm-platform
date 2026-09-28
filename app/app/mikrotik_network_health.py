"""Core 0.46 MikroTik IP address and route health from read-only snapshots."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import main as core
from app.db import SessionLocal
from app import mikrotik_workspace as workspace

router = APIRouter()


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _snapshot_rows(snapshot):
    result = (snapshot.result or {}) if snapshot else {}
    raw = result.get("data") if isinstance(result, dict) else None
    return raw if isinstance(raw, list) else []


def _normalize_ip(row):
    data = dict(row or {})
    disabled = _truthy(data.get("disabled"))
    invalid = _truthy(data.get("invalid"))
    dynamic = _truthy(data.get("dynamic"))
    if disabled:
        state = "disabled"
    elif invalid:
        state = "invalid"
    elif dynamic:
        state = "dynamic"
    else:
        state = "static"
    return {
        "address": data.get("address") or "—",
        "network": data.get("network") or "—",
        "interface": data.get("actual-interface") or data.get("interface") or "—",
        "state": state,
        "dynamic": dynamic,
        "disabled": disabled,
        "invalid": invalid,
        "comment": data.get("comment") or "",
        "raw": data,
    }


def _normalize_route(row):
    data = dict(row or {})
    disabled = _truthy(data.get("disabled"))
    active = _truthy(data.get("active"))
    dynamic = _truthy(data.get("dynamic"))
    dst = data.get("dst-address") or data.get("dst_address") or "—"
    if disabled:
        state = "disabled"
    elif active:
        state = "active"
    else:
        state = "inactive"
    return {
        "dst": dst,
        "gateway": data.get("gateway") or data.get("immediate-gw") or "—",
        "distance": data.get("distance") if data.get("distance") not in (None, "") else "—",
        "table": data.get("routing-table") or data.get("routing_table") or "main",
        "state": state,
        "active": active,
        "disabled": disabled,
        "dynamic": dynamic,
        "default": dst in {"0.0.0.0/0", "::/0"},
        "check_gateway": data.get("check-gateway") or "—",
        "comment": data.get("comment") or "",
        "raw": data,
    }


def _ip_rows(snapshot, q="", state="all"):
    rows = [_normalize_ip(item) for item in _snapshot_rows(snapshot)]
    query = (q or "").strip().lower()
    if query:
        rows = [r for r in rows if query in " ".join((r["address"], r["network"], r["interface"], r["comment"])).lower()]
    if state in {"static", "dynamic", "disabled", "invalid"}:
        rows = [r for r in rows if r["state"] == state]
    return rows


def _route_rows(snapshot, q="", state="all"):
    rows = [_normalize_route(item) for item in _snapshot_rows(snapshot)]
    query = (q or "").strip().lower()
    if query:
        rows = [r for r in rows if query in " ".join((r["dst"], r["gateway"], r["table"], r["comment"])).lower()]
    if state in {"active", "inactive", "disabled"}:
        rows = [r for r in rows if r["state"] == state]
    elif state == "default":
        rows = [r for r in rows if r["default"]]
    return rows


def _context(db, device_id):
    device = workspace._load_device(db, device_id)
    if device.vendor != "mikrotik":
        raise HTTPException(404)
    ctx = dict(workspace._workspace_context(db, device))
    ctx.setdefault("agent_transport", "unknown")
    ctx.setdefault("snapshot_supported", ctx["agent_transport"] == "modern")
    return device, ctx


@router.get("/devices/{device_id}/ip-addresses", response_class=HTMLResponse, name="mikrotik_ip_addresses")
def ip_addresses_page(request: Request, device_id: uuid.UUID, q: str = "", state: str = "all"):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        device, ctx = _context(db, device_id)
        snapshot = ctx.get("snapshots", {}).get("ip_addresses")
        all_rows = [_normalize_ip(item) for item in _snapshot_rows(snapshot)]
        stats = {
            "total": len(all_rows),
            "static": sum(1 for r in all_rows if r["state"] == "static"),
            "dynamic": sum(1 for r in all_rows if r["state"] == "dynamic"),
            "attention": sum(1 for r in all_rows if r["state"] in {"disabled", "invalid"}),
        }
        ctx.update(device=device, snapshot=snapshot, rows=_ip_rows(snapshot, q, state), stats=stats, q=q, state=state)
        return core.render(request, db, user, "mikrotik_ip_addresses.html", **ctx)


@router.get("/devices/{device_id}/routes", response_class=HTMLResponse, name="mikrotik_routes")
def routes_page(request: Request, device_id: uuid.UUID, q: str = "", state: str = "all"):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        device, ctx = _context(db, device_id)
        snapshot = ctx.get("snapshots", {}).get("routes")
        all_rows = [_normalize_route(item) for item in _snapshot_rows(snapshot)]
        stats = {
            "total": len(all_rows),
            "active": sum(1 for r in all_rows if r["state"] == "active"),
            "inactive": sum(1 for r in all_rows if r["state"] == "inactive"),
            "default": sum(1 for r in all_rows if r["default"]),
        }
        ctx.update(device=device, snapshot=snapshot, rows=_route_rows(snapshot, q, state), stats=stats, q=q, state=state)
        return core.render(request, db, user, "mikrotik_routes.html", **ctx)


def install_mikrotik_network_health(app):
    app.include_router(router)
