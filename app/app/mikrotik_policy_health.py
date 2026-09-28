"""Core 0.47 MikroTik firewall and DHCP health from read-only snapshots."""
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


def _snapshot_data(snapshot):
    result = (snapshot.result or {}) if snapshot else {}
    return result.get("data") if isinstance(result, dict) else None


def _normalize_firewall(row, kind):
    data = dict(row or {})
    disabled = _truthy(data.get("disabled"))
    action = str(data.get("action") or "—")
    chain = str(data.get("chain") or "—")
    state = "disabled" if disabled else "enabled"
    severity = "attention" if (not disabled and action.lower() in {"drop", "reject", "tarpit"}) else "normal"
    src = data.get("src-address") or data.get("src-address-list") or "—"
    dst = data.get("dst-address") or data.get("dst-address-list") or "—"
    port = data.get("dst-port") or data.get("src-port") or "—"
    return {
        "kind": kind,
        "chain": chain,
        "action": action,
        "protocol": data.get("protocol") or "—",
        "src": src,
        "dst": dst,
        "port": port,
        "in_interface": data.get("in-interface") or data.get("in-interface-list") or "—",
        "out_interface": data.get("out-interface") or data.get("out-interface-list") or "—",
        "state": state,
        "disabled": disabled,
        "severity": severity,
        "comment": data.get("comment") or "",
        "raw": data,
    }


def _firewall_all(snapshot):
    data = _snapshot_data(snapshot)
    if not isinstance(data, dict):
        return []
    rows = []
    for kind in ("filter", "nat"):
        source = data.get(kind)
        if isinstance(source, list):
            rows.extend(_normalize_firewall(item, kind) for item in source)
    return rows


def _firewall_rows(snapshot, q="", kind="all", state="all", action="all"):
    rows = _firewall_all(snapshot)
    query = (q or "").strip().lower()
    if query:
        rows = [r for r in rows if query in " ".join(str(r[k]) for k in ("chain", "action", "protocol", "src", "dst", "port", "in_interface", "out_interface", "comment")).lower()]
    if kind in {"filter", "nat"}:
        rows = [r for r in rows if r["kind"] == kind]
    if state in {"enabled", "disabled"}:
        rows = [r for r in rows if r["state"] == state]
    if action != "all":
        rows = [r for r in rows if r["action"].lower() == action.lower()]
    return rows


def _normalize_lease(row):
    data = dict(row or {})
    disabled = _truthy(data.get("disabled"))
    blocked = _truthy(data.get("blocked"))
    dynamic = _truthy(data.get("dynamic"))
    status = str(data.get("status") or "unknown").lower()
    if disabled:
        state = "disabled"
    elif blocked:
        state = "blocked"
    elif status in {"bound", "busy"}:
        state = "bound"
    else:
        state = status or "unknown"
    return {
        "address": data.get("address") or data.get("active-address") or "—",
        "mac": data.get("mac-address") or data.get("active-mac-address") or "—",
        "hostname": data.get("host-name") or data.get("active-host-name") or "—",
        "server": data.get("server") or "—",
        "state": state,
        "status": status,
        "dynamic": dynamic,
        "blocked": blocked,
        "disabled": disabled,
        "expires": data.get("expires-after") or "—",
        "last_seen": data.get("last-seen") or "—",
        "comment": data.get("comment") or "",
        "raw": data,
    }


def _dhcp_all(snapshot):
    data = _snapshot_data(snapshot)
    return [_normalize_lease(item) for item in data] if isinstance(data, list) else []


def _dhcp_rows(snapshot, q="", state="all"):
    rows = _dhcp_all(snapshot)
    query = (q or "").strip().lower()
    if query:
        rows = [r for r in rows if query in " ".join(str(r[k]) for k in ("address", "mac", "hostname", "server", "comment")).lower()]
    if state != "all":
        rows = [r for r in rows if r["state"] == state]
    return rows


def _context(db, device_id):
    device = workspace._load_device(db, device_id)
    if device.vendor != "mikrotik":
        raise HTTPException(404)
    ctx = dict(workspace._workspace_context(db, device))
    ctx.setdefault("agent_transport", "unknown")
    ctx.setdefault("snapshot_supported", ctx["agent_transport"] == "modern")
    return device, ctx


@router.get("/devices/{device_id}/firewall", response_class=HTMLResponse, name="mikrotik_firewall")
def firewall_page(request: Request, device_id: uuid.UUID, q: str = "", kind: str = "all", state: str = "all", action: str = "all"):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        device, ctx = _context(db, device_id)
        snapshot = ctx.get("snapshots", {}).get("firewall")
        all_rows = _firewall_all(snapshot)
        actions = sorted({r["action"] for r in all_rows if r["action"] != "—"}, key=str.lower)
        stats = {
            "total": len(all_rows),
            "filter": sum(1 for r in all_rows if r["kind"] == "filter"),
            "nat": sum(1 for r in all_rows if r["kind"] == "nat"),
            "disabled": sum(1 for r in all_rows if r["disabled"]),
            "blocking": sum(1 for r in all_rows if r["severity"] == "attention"),
        }
        ctx.update(device=device, snapshot=snapshot, rows=_firewall_rows(snapshot, q, kind, state, action), stats=stats, actions=actions, q=q, kind=kind, state=state, action=action)
        return core.render(request, db, user, "mikrotik_firewall.html", **ctx)


@router.get("/devices/{device_id}/dhcp-leases", response_class=HTMLResponse, name="mikrotik_dhcp_leases")
def dhcp_leases_page(request: Request, device_id: uuid.UUID, q: str = "", state: str = "all"):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        device, ctx = _context(db, device_id)
        snapshot = ctx.get("snapshots", {}).get("dhcp_leases")
        all_rows = _dhcp_all(snapshot)
        stats = {
            "total": len(all_rows),
            "bound": sum(1 for r in all_rows if r["state"] == "bound"),
            "dynamic": sum(1 for r in all_rows if r["dynamic"]),
            "attention": sum(1 for r in all_rows if r["state"] in {"blocked", "disabled"}),
        }
        states = sorted({r["state"] for r in all_rows})
        ctx.update(device=device, snapshot=snapshot, rows=_dhcp_rows(snapshot, q, state), stats=stats, states=states, q=q, state=state)
        return core.render(request, db, user, "mikrotik_dhcp_leases.html", **ctx)


def install_mikrotik_policy_health(app):
    app.include_router(router)
