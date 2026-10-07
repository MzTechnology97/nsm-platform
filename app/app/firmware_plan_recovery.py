"""Lifecycle recovery for RouterOS upgrade plans with Agent-owned phases.

Staging (download-only) and activation (preflight + ack + reboot) are executed
by the MikroTik Agent through DeviceJobs.  Without an execution window a lost
job would leave its plan in ``staging`` / ``activation_pending`` forever, and
because those states are active the Device could never receive a new plan.

This module owns three rules:

* Agent-owned firmware jobs carry an explicit execution window;
* operator cancellation withdraws jobs the Agent has not received yet, and
  refuses to cancel an activation the Agent may already be executing;
* the worker fails a plan whose phase job expired or disappeared, so the
  operator gets an explicit terminal state instead of a stuck workflow.

A late Agent report for a plan that is no longer in the matching phase is
recorded on the job but must never move the plan again (see
``firmware_package_staging.stage_complete`` and ``activation_ack``).
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.models import Device, utcnow

STAGE_JOB_TYPE = "firmware_stage"
ACTIVATE_JOB_TYPE = "firmware_activate"
STAGING_JOB_TTL = timedelta(minutes=60)
ACTIVATION_JOB_TTL = timedelta(minutes=15)
ACTIVE_JOB_STATES = ("pending", "delivered", "running")
PHASE_JOBS = {
    "staging": (STAGE_JOB_TYPE, STAGING_JOB_TTL),
    "activation_pending": (ACTIVATE_JOB_TYPE, ACTIVATION_JOB_TTL),
}
CANCELLED_JOB_ERROR = "Job ritirato: piano firmware annullato dall'operatore."
EXPIRED_JOB_ERROR = "Job firmware non completato entro la finestra di esecuzione."
PHASE_EXPIRED_ERRORS = {
    "staging": "Download pacchetti non completato entro la finestra prevista: piano chiuso, crearne uno nuovo.",
    "activation_pending": "Attivazione non eseguita dall'agent entro la finestra prevista: nessun reboot effettuato, piano chiuso.",
}


def plan_jobs(db, plan: FirmwareUpgradePlan, job_type: str) -> list[DeviceJob]:
    """Return the Device jobs of ``job_type`` created for ``plan``, newest first."""
    jobs = db.scalars(
        select(DeviceJob)
        .where(DeviceJob.device_id == plan.device_id, DeviceJob.job_type == job_type)
        .order_by(DeviceJob.created_at.desc())
    )
    plan_id = str(plan.id)
    return [job for job in jobs if str((job.payload or {}).get("plan_id") or "") == plan_id]


def withdraw_jobs_for_cancel(db, plan: FirmwareUpgradePlan, now) -> list[str]:
    """Withdraw undelivered phase jobs of a plan that is being cancelled.

    Raises 409 when an activation job has already reached the Agent: the
    router may be executing preflight/reboot, so the plan must wait for the ack
    (which is refused once the plan is no longer ``activation_pending``).
    A delivered staging job is left alone because it is download-only; its late
    report can no longer move the cancelled plan.
    """
    phase = PHASE_JOBS.get(plan.status)
    if not phase:
        return []
    job_type, _ = phase
    active = [job for job in plan_jobs(db, plan, job_type) if job.status in ACTIVE_JOB_STATES]
    if plan.status == "activation_pending" and any(job.status != "pending" for job in active):
        raise HTTPException(
            409,
            "Attivazione già consegnata al MikroTik: attendi l'esito del preflight prima di annullare.",
        )
    withdrawn = []
    for job in active:
        if job.status != "pending":
            continue
        job.status = "failed"
        job.last_error = CANCELLED_JOB_ERROR
        job.completed_at = now
        withdrawn.append(str(job.id))
    return withdrawn


def reconcile_firmware_plan_jobs(now=None) -> dict[str, int]:
    """Close plans whose Agent-owned phase can no longer complete."""
    now = now or utcnow()
    stats = {"checked": 0, "jobs_expired": 0, "plans_failed": 0}
    with SessionLocal() as db:
        plans = list(
            db.scalars(
                select(FirmwareUpgradePlan).where(FirmwareUpgradePlan.status.in_(tuple(PHASE_JOBS)))
            )
        )
        for plan in plans:
            stats["checked"] += 1
            job_type, ttl = PHASE_JOBS[plan.status]
            jobs = plan_jobs(db, plan, job_type)
            for job in jobs:
                if job.status not in ACTIVE_JOB_STATES:
                    continue
                # Jobs queued before execution windows existed have no
                # expires_at; derive the same window from their creation time.
                deadline = job.expires_at or (job.created_at + ttl)
                if deadline <= now:
                    job.status = "failed"
                    job.last_error = EXPIRED_JOB_ERROR
                    job.completed_at = now
                    stats["jobs_expired"] += 1
            if any(job.status in ACTIVE_JOB_STATES for job in jobs):
                continue
            if jobs and jobs[0].status == "success":
                # The completion endpoint owns successful transitions.
                continue

            phase = plan.status
            latest = jobs[0] if jobs else None
            plan.status = "failed"
            plan.completed_at = now
            plan.last_error = PHASE_EXPIRED_ERRORS[phase]
            stats["plans_failed"] += 1
            device = db.get(Device, plan.device_id)
            core.add_event(
                db,
                "FIRMWARE_UPGRADE_PLAN_EXPIRED",
                customer_id=device.customer_id if device else None,
                device_id=plan.device_id,
                details={
                    "plan_id": str(plan.id),
                    "phase": phase,
                    "job_id": str(latest.id) if latest else None,
                    "job_error": latest.last_error if latest else "job mancante",
                    "target_version": plan.target_version,
                },
                severity="warning",
                result="failed",
                source="worker",
            )
        db.commit()
    return stats

