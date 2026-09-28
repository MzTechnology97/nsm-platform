"""Core 0.42 real-device UX refinements for the MikroTik workspace.

This module deliberately does not change the RouterOS wire protocol. It improves
presentation, makes unsupported legacy capabilities explicit and adds a focused
read-only diagnostic result view.
"""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app import mikrotik_workspace as workspace
from app import mikrotik_telemetry as telemetry

router = APIRouter()
_original_workspace_context = workspace._workspace_context

_MEMORY_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?i?b)?\s*$", re.I)
_MEMORY_FACTORS = {
    "": 1,
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000**2,
    "mib": 1024**2,
    "gb": 1000**3,
    "gib": 1024**3,
    "tb": 1000**4,
    "tib": 1024**4,
}


def _memory_bytes(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    match = _MEMORY_RE.match(str(value))
    if not match:
        return None
    unit = (match.group(2) or "").lower()
    factor = _MEMORY_FACTORS.get(unit)
    if factor is None:
        return None
    return int(float(match.group(1)) * factor)


def human_bytes(value) -> str:
    number = _memory_bytes(value)
    if number is None:
        return "—" if value in (None, "") else str(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(number)
    index = 0
    while amount >= 1024 and index < len(units) - 1:
        amount /= 1024.0
        index += 1
    if index == 0:
        return f"{int(amount)} {units[index]}"
    return f"{amount:.1f} {units[index]}"


def agent_transport(device) -> str:
    inventory = dict(device.inventory_data or {})
    explicit = str(inventory.get("agent_transport") or "").strip().lower()
    if explicit in {"legacy", "modern"}:
        return explicit
    version = str(inventory.get("agent_version") or "").strip().lower()
    if inventory.get("legacy_agent") or version.endswith("-legacy"):
        return "legacy"
    if version:
        return "modern"
    return "unknown"


def _enhanced_context(db, device):
    ctx = _original_workspace_context(db, device)
    inventory = dict(ctx.get("inventory") or {})
    metrics = dict(ctx.get("metrics") or {})

    free_raw = metrics.get("free_memory") or inventory.get("free_memory")
    total_raw = metrics.get("total_memory") or inventory.get("total_memory")
    free_bytes = _memory_bytes(free_raw)
    total_bytes = _memory_bytes(total_raw)

    if free_raw not in (None, ""):
        metrics["free_memory"] = human_bytes(free_raw)
        inventory["free_memory"] = human_bytes(free_raw)
    if total_raw not in (None, ""):
        metrics["total_memory"] = human_bytes(total_raw)
        inventory["total_memory"] = human_bytes(total_raw)
    if free_bytes is not None and total_bytes and total_bytes > 0:
        used = max(0.0, min(100.0, (1.0 - (free_bytes / total_bytes)) * 100.0))
        metrics["memory_used_percent"] = round(used, 1)

    transport = agent_transport(device)
    ctx.update(
        {
            "inventory": inventory,
            "metrics": metrics,
            "agent_transport": transport,
            "snapshot_supported": transport == "modern",
            "telemetry_history_supported": transport == "modern",
            "support_snapshot_supported": transport == "modern",
            "legacy_capability_reason": (
                "Il transport legacy RouterOS 7.12.x non supporta questa acquisizione strutturata. "
                "Heartbeat e diagnostica di base restano disponibili."
                if transport == "legacy"
                else None
            ),
        }
    )
    return ctx


def _remove_exact_route(app, path: str, method: str = "GET"):
    method = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def _require_device(request: Request, device_id: uuid.UUID, permission: str):
    db = SessionLocal()
    try:
        user = core.current_user(request, db)
        if not user:
            return db, None, None
        if not core.has_permission(user, permission):
            raise HTTPException(403)
        device = workspace._load_device(db, device_id)
        if device.vendor != "mikrotik":
            raise HTTPException(404)
        return db, user, device
    except Exception:
        db.close()
        raise


@router.get("/devices/{device_id}/configuration", response_class=HTMLResponse, name="mikrotik_configuration_v2")
def configuration_v2(request: Request, device_id: uuid.UUID, section: str = "resources"):
    if section not in workspace.SNAPSHOT_SECTIONS:
        section = "resources"
    db, user, device = _require_device(request, device_id, "devices.read")
    if not user:
        db.close()
        return core.login_redirect()
    try:
        ctx = _enhanced_context(db, device)
        return core.render(
            request,
            db,
            user,
            "mikrotik_configuration_v2.html",
            device=device,
            active_section=section,
            snapshot_sections=workspace.SNAPSHOT_SECTIONS,
            **ctx,
        )
    finally:
        db.close()


@router.get("/devices/{device_id}/diagnostics", response_class=HTMLResponse, name="mikrotik_diagnostics_v2")
def diagnostics_v2(request: Request, device_id: uuid.UUID):
    db, user, device = _require_device(request, device_id, "monitoring.read")
    if not user:
        db.close()
        return core.login_redirect()
    try:
        ctx = _enhanced_context(db, device)
        return core.render(request, db, user, "mikrotik_diagnostics_v2.html", device=device, **ctx)
    finally:
        db.close()


@router.get(
    "/devices/{device_id}/diagnostics/jobs/{job_id}",
    response_class=HTMLResponse,
    name="mikrotik_diagnostic_result",
)
def diagnostic_result(request: Request, device_id: uuid.UUID, job_id: uuid.UUID):
    db, user, device = _require_device(request, device_id, "monitoring.read")
    if not user:
        db.close()
        return core.login_redirect()
    try:
        job = db.scalar(
            select(DeviceJob).where(DeviceJob.id == job_id, DeviceJob.device_id == device.id)
        )
        allowed = set(workspace.DIAGNOSTIC_TYPES.values())
        if not job or job.job_type not in allowed:
            raise HTTPException(404)
        ctx = _enhanced_context(db, device)
        return core.render(
            request,
            db,
            user,
            "mikrotik_diagnostic_result.html",
            device=device,
            job=job,
            **ctx,
        )
    finally:
        db.close()


def _guard_snapshot(
    request: Request,
    device_id: uuid.UUID,
    section: str,
    csrf: str = Form(...),
):
    with SessionLocal() as db:
        device = workspace._load_device(db, device_id)
        if agent_transport(device) != "modern":
            raise HTTPException(
                409,
                "Snapshot configurazione non disponibile sul transport legacy. "
                "Usare un agent moderno compatibile prima di accodare l'operazione.",
            )
    return workspace.queue_snapshot(request, device_id, section, csrf)


def _guard_diagnostic(
    request: Request,
    device_id: uuid.UUID,
    diagnostic: str,
    target: str = Form(""),
    source: str = Form(""),
    query: str = Form(""),
    csrf: str = Form(...),
):
    if diagnostic == "support_snapshot":
        with SessionLocal() as db:
            device = workspace._load_device(db, device_id)
            if agent_transport(device) != "modern":
                raise HTTPException(
                    409,
                    "Support snapshot non disponibile sul transport legacy. "
                    "Ping, traceroute, neighbor, DHCP lookup e log restano disponibili.",
                )
    return workspace.queue_diagnostic(request, device_id, diagnostic, target, source, query, csrf)


def install_mikrotik_workspace_ux(app):
    # Enhance both workspace and telemetry presentation contexts without changing
    # persisted inventory values or RouterOS payloads.
    workspace._workspace_context = _enhanced_context
    telemetry._workspace_context = _enhanced_context

    # Replace only the two detail pages where capability-aware UX is required.
    _remove_exact_route(app, "/devices/{device_id}/configuration", "GET")
    _remove_exact_route(app, "/devices/{device_id}/diagnostics", "GET")
    app.include_router(router)

    # Fail closed server-side as well: a manipulated form cannot queue a modern
    # snapshot/support operation against a legacy device.
    _remove_exact_route(app, "/devices/{device_id}/snapshot/{section}", "POST")
    _remove_exact_route(app, "/devices/{device_id}/diagnostics/{diagnostic}", "POST")
    app.add_api_route(
        "/devices/{device_id}/snapshot/{section}",
        _guard_snapshot,
        methods=["POST"],
        name="queue_mikrotik_snapshot",
    )
    app.add_api_route(
        "/devices/{device_id}/diagnostics/{diagnostic}",
        _guard_diagnostic,
        methods=["POST"],
        name="queue_mikrotik_diagnostic",
    )
