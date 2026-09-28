"""Core 0.45 MikroTik interface health built from read-only snapshots."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import main as core
from app.db import SessionLocal
from app import mikrotik_workspace as workspace
from app.mikrotik_workspace_ux import human_bytes

router = APIRouter()


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_interface(row):
    data = dict(row or {})
    running = _truthy(data.get("running"))
    disabled = _truthy(data.get("disabled"))
    if disabled:
        state = "disabled"
    elif running:
        state = "up"
    else:
        state = "down"
    rx = data.get("rx-byte") or data.get("rx_bytes")
    tx = data.get("tx-byte") or data.get("tx_bytes")
    return {
        "name": data.get("name") or "—",
        "type": data.get("type") or data.get("default-name") or "—",
        "state": state,
        "running": running,
        "disabled": disabled,
        "mac": data.get("mac-address") or data.get("mac_address") or "—",
        "mtu": data.get("actual-mtu") or data.get("mtu") or "—",
        "l2mtu": data.get("l2mtu") or "—",
        "comment": data.get("comment") or "",
        "rx": human_bytes(rx) if rx not in (None, "") else "—",
        "tx": human_bytes(tx) if tx not in (None, "") else "—",
        "raw": data,
    }


def _rows(snapshot, q="", state="all"):
    result = (snapshot.result or {}) if snapshot else {}
    raw = result.get("data") if isinstance(result, dict) else None
    normalized = [_normalize_interface(item) for item in raw] if isinstance(raw, list) else []
    query = (q or "").strip().lower()
    if query:
        normalized = [item for item in normalized if query in " ".join((item["name"], item["type"], item["mac"], item["comment"])).lower()]
    if state in {"up", "down", "disabled"}:
        normalized = [item for item in normalized if item["state"] == state]
    return normalized


def _all_rows(snapshot):
    result = (snapshot.result or {}) if snapshot else {}
    raw = result.get("data") if isinstance(result, dict) else None
    return [_normalize_interface(item) for item in raw] if isinstance(raw, list) else []


@router.get("/devices/{device_id}/interfaces", response_class=HTMLResponse, name="mikrotik_interfaces")
def interfaces_page(request: Request, device_id: uuid.UUID, q: str = "", state: str = "all"):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        device = workspace._load_device(db, device_id)
        if device.vendor != "mikrotik":
            raise HTTPException(404)
        ctx = dict(workspace._workspace_context(db, device))
        # Core 0.43 normally supplies these capability fields. Keep explicit
        # fallbacks so this view remains truthful even if the base workspace
        # context is used independently in tests or future compositions.
        ctx.setdefault("agent_transport", "unknown")
        ctx.setdefault("snapshot_supported", ctx["agent_transport"] == "modern")
        snapshot = ctx.get("snapshots", {}).get("interfaces")
        all_rows = _all_rows(snapshot)
        stats = {
            "total": len(all_rows),
            "up": sum(1 for item in all_rows if item["state"] == "up"),
            "down": sum(1 for item in all_rows if item["state"] == "down"),
            "disabled": sum(1 for item in all_rows if item["state"] == "disabled"),
        }
        ctx.update({
            "device": device,
            "rows": _rows(snapshot, q=q, state=state),
            "stats": stats,
            "snapshot": snapshot,
            "q": q,
            "state": state,
        })
        return core.render(request, db, user, "mikrotik_interfaces.html", **ctx)


def install_mikrotik_interfaces(app):
    app.include_router(router)
