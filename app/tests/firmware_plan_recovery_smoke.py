"""Firmware plans must never stay stuck or be resurrected by late Agent reports."""
import hashlib
import re
import secrets
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.firmware_plan_recovery import (
    ACTIVATION_JOB_TTL,
    STAGING_JOB_TTL,
    reconcile_firmware_plan_jobs,
)
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.models import AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Firmware-Recovery-2026"
TARGET = "7.21.1"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed_user():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"ci-fwrec-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI Firmware Recovery",
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        return user.username, user.id


def seed_device(user_id, plan_status="approved", precheck=None):
    suffix = uuid.uuid4().hex[:8]
    raw_secret = secrets.token_urlsafe(24)
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI Firmware Recovery {suffix}", code=f"FR{suffix[:6]}")
        db.add(customer)
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name=f"CI Recovery MikroTik {suffix}",
            management_source="mikrotik_agent",
            status="online",
            firmware_version="7.20.0",
            recommended_firmware_version=TARGET,
            inventory_data={"agent_transport": "modern", "agent_version": "0.20.0", "agent_privilege_profile": "ops-v1"},
        )
        db.add(device)
        db.flush()
        db.add(
            DeviceAgentCredential(
                device_id=device.id,
                agent_type="mikrotik_agent",
                secret_hash=hashlib.sha256(raw_secret.encode()).hexdigest(),
                is_active=True,
                last_used_at=now,
            )
        )
        run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_multi", completed_at=now)
        db.add(run)
        db.flush()
        plan = FirmwareUpgradePlan(
            device_id=device.id,
            target_version=TARGET,
            channel="stable",
            status=plan_status,
            backup_run_id=run.id,
            created_by=user_id,
            approved_by=user_id,
            approved_at=now,
            ready_at=now,
            precheck_data=precheck or {"installed_version": "7.20.0", "target_version": TARGET},
        )
        db.add(plan)
        db.commit()
        return device.id, plan.id, raw_secret


def login(client, username):
    token = csrf_from(client.get("/login").text)
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def post_form(client, device_id, plan_id, action, **data):
    token = csrf_from(client.get(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}").text)
    return client.post(
        f"/devices/{device_id}/firmware-upgrade/{plan_id}/{action}",
        data={"csrf": token, **data},
        follow_redirects=False,
    )


def stage(client, device_id, plan_id):
    response = post_form(client, device_id, plan_id, "stage", confirmation=f"DOWNLOAD {TARGET}")
    assert response.status_code == 303
    with SessionLocal() as db:
        job = db.scalar(
            select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "firmware_stage")
        )
        assert job is not None and job.status == "pending"
        assert job.expires_at is not None
        window = job.expires_at - job.created_at
        assert STAGING_JOB_TTL - timedelta(minutes=1) <= window <= STAGING_JOB_TTL + timedelta(minutes=1)
        return job.id


def plan_status(plan_id):
    with SessionLocal() as db:
        return db.get(FirmwareUpgradePlan, plan_id).status


def job_state(job_id):
    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        return job.status, job.last_error


def check_cancel_during_staging_withdraws_pending_job(client, user_id):
    device_id, plan_id, _ = seed_device(user_id)
    job_id = stage(client, device_id, plan_id)
    page = client.get(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}")
    assert "Annulla piano" in page.text, "staging plans must expose cancellation in the GUI"

    cancel = post_form(client, device_id, plan_id, "cancel")
    assert cancel.status_code == 303
    feedback = client.get(cancel.headers["location"])
    assert "Piano annullato" in feedback.text
    assert plan_status(plan_id) == "cancelled"
    status, error = job_state(job_id)
    assert status == "failed" and "annullato" in error


def check_late_staging_report_cannot_resurrect_cancelled_plan(client, user_id):
    device_id, plan_id, raw_secret = seed_device(user_id)
    job_id = stage(client, device_id, plan_id)
    headers = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw_secret}
    heartbeat = client.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"inventory": {}})
    assert heartbeat.status_code == 200
    assert str(job_id) in {job["id"] for job in heartbeat.json()["jobs"]}
    assert job_state(job_id)[0] == "delivered"

    cancel = post_form(client, device_id, plan_id, "cancel")
    assert cancel.status_code == 303
    assert plan_status(plan_id) == "cancelled"
    assert job_state(job_id)[0] == "delivered", "delivered download-only job is left to finish"

    complete = client.post(
        f"/api/v1/agents/mikrotik/firmware-stage/{job_id}/complete",
        headers=headers,
        json={"status": "success", "error": "", "result": {"target_version": TARGET, "download_only": True}},
    )
    assert complete.status_code == 200
    assert plan_status(plan_id) == "cancelled", "late staging success must not resurrect a cancelled plan"
    assert job_state(job_id)[0] == "success"
    with SessionLocal() as db:
        event = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.device_id == device_id, AuditEvent.event_type == "FIRMWARE_PACKAGE_STAGING_COMPLETED")
            .order_by(AuditEvent.timestamp.desc())
        )
        assert event.details["plan_updated"] is False
        assert event.details["plan_status"] == "cancelled"

    activate = post_form(client, device_id, plan_id, "activate", confirmation=f"ACTIVATE {TARGET}")
    assert activate.status_code == 303
    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "firmware_activate")
            )
        )
        assert jobs == [], "a cancelled plan must never queue a reboot"


def check_expired_staging_closes_plan(client, user_id):
    device_id, plan_id, _ = seed_device(user_id)
    job_id = stage(client, device_id, plan_id)
    stats = reconcile_firmware_plan_jobs(utcnow())
    assert plan_status(plan_id) == "staging", stats

    reconcile_firmware_plan_jobs(utcnow() + STAGING_JOB_TTL + timedelta(minutes=1))
    assert plan_status(plan_id) == "failed"
    assert job_state(job_id)[0] == "failed"
    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        assert "Download pacchetti non completato" in plan.last_error
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.device_id == device_id,
                AuditEvent.event_type == "FIRMWARE_UPGRADE_PLAN_EXPIRED",
            )
        )
        assert event is not None and event.details["phase"] == "staging"


def check_legacy_stuck_job_without_window_is_recovered(user_id):
    device_id, plan_id, _ = seed_device(user_id, plan_status="staging")
    created = utcnow() - STAGING_JOB_TTL - timedelta(minutes=5)
    with SessionLocal() as db:
        job = DeviceJob(
            device_id=device_id,
            job_type="firmware_stage",
            status="delivered",
            created_at=created,
            delivered_at=created,
            payload={"plan_id": str(plan_id), "target_version": TARGET, "download_only": True},
        )
        db.add(job)
        db.commit()
        job_id = job.id
    stats = reconcile_firmware_plan_jobs()
    assert stats["jobs_expired"] >= 1 and stats["plans_failed"] >= 1
    assert plan_status(plan_id) == "failed"
    assert job_state(job_id)[0] == "failed"


def check_missing_phase_job_closes_plan(user_id):
    device_id, plan_id, _ = seed_device(user_id, plan_status="activation_pending")
    reconcile_firmware_plan_jobs()
    assert plan_status(plan_id) == "failed"


def staged_precheck():
    return {
        "installed_version": "7.20.0",
        "target_version": TARGET,
        "staging": {"download_only": True, "target_version": TARGET},
    }


def queue_activation(client, device_id, plan_id):
    response = post_form(client, device_id, plan_id, "activate", confirmation=f"ACTIVATE {TARGET}")
    assert response.status_code == 303
    assert plan_status(plan_id) == "activation_pending"
    with SessionLocal() as db:
        job = db.scalar(
            select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "firmware_activate")
        )
        assert job.expires_at is not None
        assert job.expires_at - job.created_at <= ACTIVATION_JOB_TTL + timedelta(minutes=1)
        return job.id


def check_activation_cancel_rules(client, user_id):
    # Queued but not delivered: cancellation withdraws the job, no reboot can follow.
    device_id, plan_id, raw_secret = seed_device(user_id, plan_status="staged", precheck=staged_precheck())
    job_id = queue_activation(client, device_id, plan_id)
    page = client.get(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}")
    assert "Annulla piano" in page.text
    cancel = post_form(client, device_id, plan_id, "cancel")
    assert cancel.status_code == 303
    assert plan_status(plan_id) == "cancelled"
    assert job_state(job_id)[0] == "failed"
    headers = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw_secret}
    heartbeat = client.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"inventory": {}})
    assert str(job_id) not in {job["id"] for job in heartbeat.json()["jobs"]}

    # Delivered: the router may be running preflight, so cancellation is refused in the GUI.
    device_id, plan_id, raw_secret = seed_device(user_id, plan_status="staged", precheck=staged_precheck())
    job_id = queue_activation(client, device_id, plan_id)
    headers = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw_secret}
    heartbeat = client.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"inventory": {}})
    assert str(job_id) in {job["id"] for job in heartbeat.json()["jobs"]}
    cancel = post_form(client, device_id, plan_id, "cancel")
    assert cancel.status_code == 303
    refused = client.get(cancel.headers["location"])
    assert "Attivazione già consegnata" in refused.text
    assert plan_status(plan_id) == "activation_pending"

    # Never acknowledged: the worker closes the plan without any reboot evidence.
    reconcile_firmware_plan_jobs(utcnow() + ACTIVATION_JOB_TTL + timedelta(minutes=1))
    assert plan_status(plan_id) == "failed"
    assert job_state(job_id)[0] == "failed"
    ack = client.post(
        f"/api/v1/agents/mikrotik/firmware-activate/{job_id}/ack",
        headers=headers,
        json={"status": "accepted", "result": {"target_version": TARGET}},
    )
    assert ack.status_code == 409, "a late ack must be refused so the Agent cancels the reboot"


def main():
    username, user_id = seed_user()
    client = TestClient(app)
    login(client, username)
    check_cancel_during_staging_withdraws_pending_job(client, user_id)
    check_late_staging_report_cannot_resurrect_cancelled_plan(client, user_id)
    check_expired_staging_closes_plan(client, user_id)
    check_legacy_stuck_job_without_window_is_recovered(user_id)
    check_missing_phase_job_closes_plan(user_id)
    check_activation_cancel_rules(client, user_id)
    print("Firmware plan recovery smoke passed")


if __name__ == "__main__":
    main()
