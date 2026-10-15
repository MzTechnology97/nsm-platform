"""Device Diagnostics tab for the new interface (phase 1d), MikroTik only.

- ``GET  /api/ui/v1/devices/{id}/diagnostics``: what can run (transport,
  support snapshot, online, permission) and the latest diagnostics with their
  structured result (ping replies and statistics, traceroute hops, neighbors,
  DHCP leases, device log), parsed by ``mikrotik_diagnostic_views``;
- ``POST /api/ui/v1/devices/{id}/diagnostics/{diagnostic}``: queues one of the
  allow-listed diagnostics through the same guarded function as the classic
  forms (target/source/query validation, legacy support-snapshot rule).

- ``GET  /api/ui/v1/devices/{id}/diagnostics/jobs/{job_id}/output``: the raw data returned
  by the agent (support snapshots have no structured view); JSON export keeps its URL.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app import mikrotik_diagnostic_views as views
from app import mikrotik_workspace as workspace
from app import mikrotik_workspace_ux as workspace_ux
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.ui_api import brand_icon, require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
KINDS = {job_type: key for key, job_type in workspace.DIAGNOSTIC_TYPES.items()}


def _iso(value):
    return value.isoformat() if value else None


def _device_ref(device):
    return {"id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device)} if device is not None else None


def _result(job, db) -> dict | None:
    data = views.view(job, db)
    if data is None:
        return None
    if data["kind"] in ("neighbors", "dhcp"):
        data = {**data, "rows": [{**row, "device": _device_ref(row.get("device"))} for row in data["rows"]]}
    return data


def job_row(job, db) -> dict:
    payload = job.payload or {}
    base = f"/devices/{job.device_id}/diagnostics/jobs/{job.id}"
    return {
        "id": str(job.id), "diagnostic": KINDS.get(job.job_type, job.job_type), "status": job.status,
        "created_at": _iso(job.created_at), "completed_at": _iso(job.completed_at),
        "target": payload.get("target"), "source": payload.get("source") or None, "query": payload.get("query"),
        "error": job.last_error if job.status not in ("pending", "delivered", "running", "success") else None,
        "result": _result(job, db),
        "export_url": f"{base}/export.json" if job.status == "success" else None,
    }


def _mikrotik(db, device_id):
    device = workspace._load_device(db, device_id)
    if device.vendor != "mikrotik":
        raise HTTPException(404, "Diagnostica disponibile solo per MikroTik.")
    return device


@router.get("/devices/{device_id}/diagnostics")
def device_diagnostics(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "monitoring.read")
        device = _mikrotik(db, device_id)
        transport = workspace_ux.agent_transport(device)
        jobs = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device.id, DeviceJob.job_type.in_(views.DIAGNOSTIC_JOBS))
                               .order_by(DeviceJob.created_at.desc()).limit(views.RECENT_LIMIT)))
        return {
            "transport": transport,
            "online": device.status == "online",
            "support_snapshot": transport == "modern" or (transport == "legacy" and workspace_ux._legacy_snapshots(device)),
            "traceroute": not str(device.firmware_version or "").startswith("6."),
            "can_run": core.has_permission(user, "devices.write"),
            "jobs": [job_row(job, db) for job in jobs],
        }



@router.get("/devices/{device_id}/diagnostics/jobs/{job_id}/output")
def diagnostic_output(request: Request, device_id: uuid.UUID, job_id: uuid.UUID):
    """Raw result of one diagnostic run (``data`` from the agent, or its text ``output``)."""
    with SessionLocal() as db:
        ui_user(request, db, "monitoring.read")
        device = _mikrotik(db, device_id)
        job = db.scalar(select(DeviceJob).where(DeviceJob.id == job_id, DeviceJob.device_id == device.id, DeviceJob.job_type.in_(views.DIAGNOSTIC_JOBS)))
        if job is None:
            raise HTTPException(404)
        result = job.result or {}
        return {"id": str(job.id), "diagnostic": KINDS.get(job.job_type, job.job_type), "status": job.status,
                "data": result.get("data"), "output": result.get("output")}

@router.post("/devices/{device_id}/diagnostics/{diagnostic}", dependencies=[Depends(require_csrf)])
def device_diagnostic_run(request: Request, device_id: uuid.UUID, diagnostic: str, body: dict = Body(default={})):
    if diagnostic not in workspace.DIAGNOSTIC_TYPES:
        raise HTTPException(404, "Diagnostica non supportata.")
    with SessionLocal() as db:
        ui_user(request, db, "devices.write")
        _mikrotik(db, device_id)
    field = lambda key: str((body or {}).get(key) or "")[:300]  # noqa: E731
    try:
        # Same guarded entry point as the classic form; the CSRF header was already checked.
        workspace_ux._guard_diagnostic(request, device_id, diagnostic, field("target"), field("source"), field("query"),
                                       csrf=request.headers.get("X-CSRF-Token", ""))
    except HTTPException as exc:
        raise HTTPException(exc.status_code, {"code": "refused", "message": str(exc.detail)}) from exc
    with SessionLocal() as db:
        job = db.scalar(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == workspace.DIAGNOSTIC_TYPES[diagnostic])
                        .order_by(DeviceJob.created_at.desc()).limit(1))
        return {"queued": True, "job": job_row(job, db) if job else None}


def install_ui_device_diagnostics(app) -> None:
    app.include_router(router)
