"""Real-device UX helpers for Core 0.40.

The module intentionally does not change RouterOS transport or job execution.
It normalizes presentation data and exposes read-only diagnostic details.
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import main as core
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.mikrotik_workspace import _load_device

router = APIRouter()
DIAGNOSTIC_JOB_TYPES = {
    "diagnostic_ping": "Ping",
    "diagnostic_traceroute": "Traceroute",
    "diagnostic_neighbors": "Neighbor discovery",
    "diagnostic_dhcp_lookup": "DHCP lookup",
    "diagnostic_logs": "Log dispositivo",
    "support_snapshot": "Support snapshot",
}
LEGACY_UNAVAILABLE = {
    "configuration_snapshots": "Gli snapshot strutturati richiedono il transport moderno RouterOS 7.13+.",
    "telemetry_history": "La telemetria storica completa richiede il transport moderno RouterOS 7.13+.",
    "backup_https": "Il backup HTTPS chunked richiede il transport moderno RouterOS 7.13+.",
}


def human_bytes(value):
    if value in (None, "", "—"):
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    index = 0
    while abs(number) >= 1024 and index < len(units) - 1:
        number /= 1024.0
        index += 1
    if index == 0:
        return f"{int(number)} {units[index]}"
    return f"{number:.1f} {units[index]}"


def percent(value):
    if value in (None, "", "—"):
        return "—"
    text = str(value).strip()
    if text.endswith("%"):
        return text
    try:
        number = float(text)
    except ValueError:
        return text
    return f"{number:g}%"


def memory_usage(free_value, total_value):
    try:
        free = float(free_value)
        total = float(total_value)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    used_pct = max(0.0, min(100.0, (1.0 - free / total) * 100.0))
    return round(used_pct, 1)


def observed_mac(device, inventory: dict):
    for value in (
        device.primary_mac,
        inventory.get("primary_mac"),
        inventory.get("mac"),
        inventory.get("mac_address"),
        inventory.get("base_mac"),
    ):
        if value:
            return str(value)
    return "—"


def transport_for(device, inventory: dict):
    explicit = str(inventory.get("agent_transport") or "").strip().lower()
    if explicit in {"legacy", "modern"}:
        return explicit
    agent_version = str(inventory.get("agent_version") or "").lower()
    if "legacy" in agent_version:
        return "legacy"
    if agent_version:
        return "modern"
    return "unknown"


def capability_state(device, inventory: dict):
    transport = transport_for(device, inventory)
    modern = transport == "modern"
    return {
        "transport": transport,
        "configuration_snapshots": modern,
        "telemetry_history": modern,
        "backup_https": modern,
        "diagnostics": transport in {"legacy", "modern"},
        "firmware_readiness": transport in {"legacy", "modern"},
        "legacy_messages": LEGACY_UNAVAILABLE if transport == "legacy" else {},
    }


def normalize_workspace_context(device, ctx: dict):
    inventory = dict(ctx.get("inventory") or device.inventory_data or {})
    metrics = dict(ctx.get("metrics") or {})
    free = metrics.get("free_memory", inventory.get("free_memory"))
    total = metrics.get("total_memory", inventory.get("total_memory"))
    ctx["display_mac"] = observed_mac(device, inventory)
    ctx["display_cpu"] = percent(metrics.get("cpu_load", inventory.get("cpu_load")))
    ctx["display_free_memory"] = human_bytes(free)
    ctx["display_total_memory"] = human_bytes(total)
    ctx["display_memory_used_pct"] = memory_usage(free, total)
    ctx["device_capabilities"] = capability_state(device, inventory)
    return ctx


def _safe_result(job: DeviceJob):
    result = dict(job.result or {})
    payload = dict(job.payload or {})
    output = result.get("output")
    data = result.get("data")
    if isinstance(output, str):
        formatted = output[:200_000]
        kind = "text"
    elif data is not None:
        formatted = json.dumps(data, indent=2, ensure_ascii=False, default=str)[:200_000]
        kind = "json"
    elif result:
        formatted = json.dumps(result, indent=2, ensure_ascii=False, default=str)[:200_000]
        kind = "json"
    else:
        formatted = job.last_error or "Nessun output ricevuto."
        kind = "text"
    return payload, formatted, kind


@router.get("/devices/{device_id}/diagnostics/jobs/{job_id}", response_class=HTMLResponse, name="mikrotik_diagnostic_result")
def diagnostic_result(request: Request, device_id: uuid.UUID, job_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "monitoring.read"):
            raise HTTPException(403)
        device = _load_device(db, device_id)
        if device.vendor != "mikrotik":
            raise HTTPException(404)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type not in DIAGNOSTIC_JOB_TYPES:
            raise HTTPException(404)
        payload, formatted, output_kind = _safe_result(job)
        core.add_event(
            db,
            "DEVICE_DIAGNOSTIC_RESULT_VIEWED",
            actor=user,
            customer_id=device.customer_id,
            device_id=device.id,
            details={"job_id": str(job.id), "job_type": job.job_type},
            source="portal",
        )
        db.commit()
        return core.render(
            request,
            db,
            user,
            "diagnostic_result.html",
            device=device,
            job=job,
            diagnostic_label=DIAGNOSTIC_JOB_TYPES[job.job_type],
            payload=payload,
            formatted_output=formatted,
            output_kind=output_kind,
        )


def install_real_device_ux(app, templates):
    templates.env.globals["human_bytes"] = human_bytes
    templates.env.globals["fmt_percent"] = percent
    templates.env.globals["memory_usage"] = memory_usage
    app.include_router(router)
