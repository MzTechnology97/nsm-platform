"""Idempotency guards for MikroTik Agent job completion endpoints.

A completion retry must not mutate a job that already reached a terminal state.
This is especially important after maintenance expires a stale delivered job:
a late Agent response must not resurrect it as successful.

Dedicated backup, firmware-readiness and firmware-staging completion endpoints
are guarded by the same terminal-state contract so late/duplicate reports cannot
rewrite terminal DeviceJobs or repeat their domain side effects.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request

from app import main as core
from app import mikrotik_agent as agent_module
from app import mikrotik_backup_agent as backup_agent_module
from app import mikrotik_firmware_readiness as firmware_readiness_module
from app import firmware_package_staging as firmware_staging_module
from app import mikrotik_legacy_jobs as legacy_jobs_module
from app.agent_models import DeviceJob
from app.db import SessionLocal

router = APIRouter()
TERMINAL_JOB_STATUSES = {"success", "failed"}
GUARD_ROUTE_NAMES = {
    "guarded_mikrotik_job_complete",
    "guarded_mikrotik_legacy_job_complete",
    "guarded_mikrotik_backup_job_complete",
    "guarded_mikrotik_firmware_readiness_complete",
    "guarded_mikrotik_firmware_stage_complete",
}
MODERN_COMPLETE_PATH = "/api/v1/agents/mikrotik/jobs/{job_id}/complete"
LEGACY_COMPLETE_PATH = "/api/v1/agents/mikrotik/legacy/jobs/{job_id}/complete"
BACKUP_COMPLETE_PATH = "/api/v1/agents/mikrotik/backup-jobs/{job_id}/complete"
FIRMWARE_READINESS_COMPLETE_PATH = "/api/v1/agents/mikrotik/firmware-readiness/{job_id}/complete"
FIRMWARE_STAGE_COMPLETE_PATH = "/api/v1/agents/mikrotik/firmware-stage/{job_id}/complete"


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
            db.commit()
            return response

    return await agent_module.mikrotik_job_complete(request, job_id)


async def guarded_legacy_job_complete(
    request: Request,
    job_id: uuid.UUID,
    status: str = Query("failed"),
):
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


async def guarded_backup_job_complete(request: Request, job_id: uuid.UUID):
    """Make the dedicated MikroTik backup completion endpoint idempotent."""
    payload = await agent_module._json_body(request)
    requested_status = str(payload.get("status", "failed")).strip().lower()
    if requested_status not in TERMINAL_JOB_STATUSES:
        raise HTTPException(400, "Stato job non valido.")

    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type != "backup_mikrotik":
            raise HTTPException(404, "Backup job non trovato.")
        if job.status in TERMINAL_JOB_STATUSES:
            response = _terminal_response(job)
            db.commit()
            return response
        if job.status == "pending":
            # Maintenance re-queued the job after a timeout. A report arriving
            # now belongs to the abandoned attempt and must not decide the retry.
            core.add_event(
                db,
                "BACKUP_STALE_ATTEMPT_REPORT_IGNORED",
                customer_id=device.customer_id,
                device_id=device.id,
                details={
                    "job_id": str(job.id),
                    "reported_status": requested_status,
                    "attempts": job.attempts,
                },
                severity="warning",
                result="failed",
                source="mikrotik_agent",
            )
            db.commit()
            return {"status": "ok", "stale_attempt": True, "job_status": job.status}

    return await backup_agent_module.backup_aware_job_complete(request, job_id)


async def guarded_firmware_readiness_complete(request: Request, job_id: uuid.UUID):
    """Keep terminal firmware-readiness jobs and observed device state immutable."""
    raw = await request.body()
    if len(raw) > firmware_readiness_module.MAX_RESULT_BODY:
        raise HTTPException(413, "Risultato firmware troppo grande.")
    payload = await agent_module._json_body(request)

    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if (
            not job
            or job.device_id != device.id
            or job.job_type != firmware_readiness_module.JOB_TYPE
        ):
            raise HTTPException(404, "Job firmware non trovato.")
        requested_status = firmware_readiness_module._clean(payload.get("status"), 30) or "failed"
        if requested_status not in TERMINAL_JOB_STATUSES:
            raise HTTPException(400, "Stato firmware non valido.")
        if job.status in TERMINAL_JOB_STATUSES:
            response = _terminal_response(job)
            db.commit()
            return response

    return await firmware_readiness_module.firmware_readiness_complete(request, job_id)


async def guarded_firmware_stage_complete(request: Request, job_id: uuid.UUID):
    """Prevent duplicate staging reports from rewriting a terminal plan/job."""
    raw = await request.body()
    if len(raw) > firmware_staging_module.MAX_RESULT_BODY:
        raise HTTPException(413, "Risultato staging troppo grande.")
    payload = await agent_module._json_body(request)

    with SessionLocal() as db:
        device, _ = agent_module._authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if (
            not job
            or job.device_id != device.id
            or job.job_type != firmware_staging_module.JOB_TYPE
        ):
            raise HTTPException(404, "Job staging non trovato.")
        requested_status = str(payload.get("status") or "failed").strip().lower()
        if requested_status not in TERMINAL_JOB_STATUSES:
            raise HTTPException(400, "Stato staging non valido.")
        if job.status in TERMINAL_JOB_STATUSES:
            response = _terminal_response(job)
            db.commit()
            return response

    return await firmware_staging_module.stage_complete(request, job_id)


def install_mikrotik_job_completion_guard(app) -> None:
    """Register completion guards directly on the final application.

    Direct registration plus explicit promotion guarantees that each guard is
    the first matching route while preserving the original handler as the
    first-completion contract.
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
    app.add_api_route(
        BACKUP_COMPLETE_PATH,
        guarded_backup_job_complete,
        methods=["POST"],
        name="guarded_mikrotik_backup_job_complete",
    )
    app.add_api_route(
        FIRMWARE_READINESS_COMPLETE_PATH,
        guarded_firmware_readiness_complete,
        methods=["POST"],
        name="guarded_mikrotik_firmware_readiness_complete",
    )
    app.add_api_route(
        FIRMWARE_STAGE_COMPLETE_PATH,
        guarded_firmware_stage_complete,
        methods=["POST"],
        name="guarded_mikrotik_firmware_stage_complete",
    )
    _promote_guard_routes(app)
