"""Idempotency guard for generic MikroTik Agent job completion endpoints.

A completion retry must not mutate a job that already reached a terminal state.
This is especially important after maintenance expires a stale delivered job:
a late Agent response must not resurrect it as successful.

Backup jobs use their dedicated completion/finalization lifecycle and are not
handled here.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request

from app import mikrotik_agent as agent_module
from app import mikrotik_legacy_jobs as legacy_jobs_module
from app.agent_models import DeviceJob
from app.db import SessionLocal

router = APIRouter()
TERMINAL_JOB_STATUSES = {"success", "failed"}
GUARD_ROUTE_NAMES = {
    "guarded_mikrotik_job_complete",
    "guarded_mikrotik_legacy_job_complete",
}
MODERN_COMPLETE_PATH = "/api/v1/agents/mikrotik/jobs/{job_id}/complete"
LEGACY_COMPLETE_PATH = "/api/v1/agents/mikrotik/legacy/jobs/{job_id}/complete"


def _remove_existing_guard_routes(app) -> None:
    """Make repeated installation safe without depending on path internals."""
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if getattr(route, "name", None) not in GUARD_ROUTE_NAMES
    ]


def _promote_guard_routes(app) -> None:
    """Put guarded completion routes before compatibility/original routes."""
    promoted = [
        route
        for route in app.router.routes
        if getattr(route, "name", None) in GUARD_ROUTE_NAMES
    ]
    promoted_ids = {id(route) for route in promoted}
    remaining = [route for route in app.router.routes if id(route) not in promoted_ids]
    app.router.routes[:] = promoted + remaining


def _terminal_response(job: DeviceJob) -> dict:
    return {
        "status": "ok",
        "already_terminal": True,
        "job_status": job.status,
    }


async def guarded_modern_job_complete(request: Request, job_id: uuid.UUID):
    # Preserve the original body/status validation before applying idempotency.
    payload = await agent_module._json_body(request)
    requested_status = agent_module._string(payload.get("status"), 30) or "failed"
    if requested_status not in TERMINAL_JOB_STATUSES:
        raise HTTPException(400, "Stato job non valido.")

    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id:
            raise HTTPException(404, "Job non trovato.")
        if job.status in TERMINAL_JOB_STATUSES:
            response = _terminal_response(job)
            # _authenticate_agent updates last_used_at; persist that normal
            # credential activity even though the duplicate completion is a no-op.
            db.commit()
            return response

    # Request.body() is cached by Starlette, so the canonical handler can parse
    # the same payload while retaining all existing result/audit behavior.
    return await agent_module.mikrotik_job_complete(request, job_id)


async def guarded_legacy_job_complete(
    request: Request,
    job_id: uuid.UUID,
    status: str = Query("failed"),
):
    # Preserve the legacy endpoint's body-size and status contract.
    raw = await request.body()
    if len(raw) > legacy_jobs_module.MAX_LEGACY_RESULT:
        raise HTTPException(413, "Risultato job legacy troppo grande.")
    normalized = status.strip().lower()
    if normalized not in TERMINAL_JOB_STATUSES:
        raise HTTPException(400, "Stato job legacy non valido.")

    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if (
            not job
            or job.device_id != device.id
            or job.job_type not in legacy_jobs_module.LEGACY_JOB_TYPES
        ):
            raise HTTPException(404, "Job legacy non trovato.")
        if job.status in TERMINAL_JOB_STATUSES:
            response = _terminal_response(job)
            db.commit()
            return response

    return await legacy_jobs_module.legacy_job_complete(request, job_id, normalized)


def install_mikrotik_job_completion_guard(app) -> None:
    """Register canonical completion guards directly on the final application.

    Do not depend on matching/removing historical routes by ``route.path``.
    FastAPI/Starlette route metadata can differ across versions and compatibility
    layers.  Direct registration plus explicit promotion guarantees that the
    guard is the first matching route while preserving the original handlers as
    fallback contracts.
    """
    _remove_existing_guard_routes(app)
    app.add_api_route(
        MODERN_COMPLETE_PATH,
        guarded_modern_job_complete,
        methods=["POST"],
        name="guarded_mikrotik_job_complete",
    )
    app.add_api_route(
        LEGACY_COMPLETE_PATH,
        guarded_legacy_job_complete,
        methods=["POST"],
        name="guarded_mikrotik_legacy_job_complete",
    )
    _promote_guard_routes(app)
