"""Idempotency guard for MikroTik firmware-readiness completion.

Firmware readiness completion updates both the DeviceJob and the Device firmware
inventory. A late or duplicate Agent report must therefore be acknowledged
without replacing an already authoritative terminal result.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, Request

from app import mikrotik_agent as agent_module
from app import mikrotik_firmware_readiness as readiness_module
from app.agent_models import DeviceJob
from app.db import SessionLocal

TERMINAL_JOB_STATUSES = {"success", "failed"}
GUARD_ROUTE_NAME = "guarded_mikrotik_firmware_readiness_complete"
COMPLETE_PATH = "/api/v1/agents/mikrotik/firmware-readiness/{job_id}/complete"


async def guarded_firmware_readiness_complete(request: Request, job_id: uuid.UUID):
    raw = await request.body()
    if len(raw) > readiness_module.MAX_RESULT_BODY:
        raise HTTPException(413, "Risultato firmware troppo grande.")
    payload = await agent_module._json_body(request)
    requested_status = readiness_module._clean(payload.get("status"), 30) or "failed"
    if requested_status not in TERMINAL_JOB_STATUSES:
        raise HTTPException(400, "Stato firmware non valido.")

    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if (
            not job
            or job.device_id != device.id
            or job.job_type != readiness_module.JOB_TYPE
        ):
            raise HTTPException(404, "Job firmware non trovato.")
        if job.status in TERMINAL_JOB_STATUSES:
            response = {
                "status": "ok",
                "already_terminal": True,
                "job_status": job.status,
            }
            # Authentication refreshes credential.last_used_at. Persist that
            # activity while keeping the terminal job and Device state intact.
            db.commit()
            return response

    # Starlette caches Request.body(), so the canonical handler can safely
    # parse the same payload and remains the single first-completion authority.
    return await readiness_module.firmware_readiness_complete(request, job_id)


def install_mikrotik_firmware_readiness_completion_guard(app) -> None:
    """Register and promote the guarded machine-facing completion route."""
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if getattr(route, "name", None) != GUARD_ROUTE_NAME
    ]
    app.add_api_route(
        COMPLETE_PATH,
        guarded_firmware_readiness_complete,
        methods=["POST"],
        name=GUARD_ROUTE_NAME,
    )
    guarded = [
        route for route in app.router.routes if getattr(route, "name", None) == GUARD_ROUTE_NAME
    ]
    guarded_ids = {id(route) for route in guarded}
    app.router.routes[:] = guarded + [
        route for route in app.router.routes if id(route) not in guarded_ids
    ]
