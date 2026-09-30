"""Idempotent completion contract for modern MikroTik Agent jobs.

RouterOS can retry a completion POST when the HTTP response is lost or ambiguous.
The base completion endpoint historically accepted every retry and rewrote an
already-terminal DeviceJob, producing duplicate audit events and allowing a late
completion to change the recorded outcome.  This focused adapter replaces only
the generic modern completion route and leaves backup/self-update endpoints to
their domain-specific handlers.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, Request
from sqlalchemy import select

from app import main as core
from app import mikrotik_agent as agent
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.models import utcnow

TERMINAL_JOB_STATES = {"success", "failed"}
COMPLETABLE_JOB_STATES = {"delivered", "running"}


def _remove_post(app, path: str) -> None:
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and "POST" in (getattr(route, "methods", set()) or set())
        )
    ]


def install_mikrotik_job_completion_guard(app) -> None:
    """Replace the generic modern completion endpoint with an idempotent one."""
    path = "/api/v1/agents/mikrotik/jobs/{job_id}/complete"
    _remove_post(app, path)

    @app.post(path, name="mikrotik_job_complete_idempotent")
    async def mikrotik_job_complete_idempotent(request: Request, job_id: uuid.UUID):
        payload = await agent._json_body(request)
        status = agent._string(payload.get("status"), 30) or "failed"
        if status not in TERMINAL_JOB_STATES:
            raise HTTPException(400, "Stato job non valido.")

        with SessionLocal() as db:
            device, _ = agent._authenticate_agent(db, request)
            job = db.scalar(
                select(DeviceJob)
                .where(DeviceJob.id == job_id)
                .with_for_update()
            )
            if not job or job.device_id != device.id:
                raise HTTPException(404, "Job non trovato.")

            if job.status in TERMINAL_JOB_STATES:
                # A retry after an ambiguous/lost HTTP response must not mutate
                # the recorded result and must not emit a second completion event.
                db.commit()
                return {
                    "status": "ok",
                    "idempotent": True,
                    "job_status": job.status,
                }

            if job.status not in COMPLETABLE_JOB_STATES:
                raise HTTPException(409, "Job non ancora consegnato o non completabile.")

            job.status = status
            job.result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            job.last_error = agent._string(payload.get("error"), 4000)
            job.completed_at = utcnow()
            core.add_event(
                db,
                "DEVICE_JOB_COMPLETED",
                customer_id=device.customer_id,
                device_id=device.id,
                details={"job_id": str(job.id), "job_type": job.job_type, "status": status},
                severity="warning" if status == "failed" else "info",
                result=status,
                source="mikrotik_agent",
            )
            db.commit()
            return {"status": "ok", "idempotent": False, "job_status": status}
