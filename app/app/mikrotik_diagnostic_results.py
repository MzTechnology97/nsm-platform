"""Core 0.48 structured diagnostic result page."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceJob
from app import mikrotik_workspace as workspace
from app import mikrotik_workspace_ux as workspace_ux
from app.mikrotik_diagnostic_views import build_diagnostic_view

router = APIRouter()


@router.get(
    "/devices/{device_id}/diagnostics/jobs/{job_id}",
    response_class=HTMLResponse,
    name="mikrotik_diagnostic_result",
)
def diagnostic_result_v2(request: Request, device_id: uuid.UUID, job_id: uuid.UUID):
    db, user, device = workspace_ux._require_device(request, device_id, "monitoring.read")
    if not user:
        db.close()
        return core.login_redirect()
    try:
        job = db.scalar(select(DeviceJob).where(DeviceJob.id == job_id, DeviceJob.device_id == device.id))
        if not job or job.job_type not in set(workspace.DIAGNOSTIC_TYPES.values()):
            raise HTTPException(404)
        ctx = workspace_ux._enhanced_context(db, device)
        view = build_diagnostic_view(job)
        return core.render(
            request,
            db,
            user,
            "mikrotik_diagnostic_result_v2.html",
            device=device,
            job=job,
            diagnostic_view=view,
            **ctx,
        )
    finally:
        db.close()


def install_mikrotik_diagnostic_results(app):
    path = "/devices/{device_id}/diagnostics/jobs/{job_id}"
    app.router.routes[:] = [
        route for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and "GET" in (getattr(route, "methods", set()) or set())
        )
    ]
    app.include_router(router)
