"""A report from an abandoned backup attempt must not decide the re-queued retry."""
import hashlib
import secrets
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_maintenance import recover_stale_jobs
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_backup_models import MikrotikBackupJobSecret
from app.models import AuditEvent, BackupPolicy, BackupRun, Customer, Device, utcnow
from app.secret_vault import encrypt_text


def seed():
    suffix = uuid.uuid4().hex[:8]
    raw_secret = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        customer = Customer(name=f"CI Stale Attempt {suffix}", code=f"SA{suffix[:6]}")
        db.add(customer)
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name=f"CI Stale Attempt Router {suffix}",
            management_source="mikrotik_agent",
            status="online",
        )
        db.add(device)
        db.flush()
        db.add(
            DeviceAgentCredential(
                device_id=device.id,
                agent_type="mikrotik_agent",
                secret_hash=hashlib.sha256(raw_secret.encode()).hexdigest(),
                is_active=True,
                last_used_at=utcnow(),
            )
        )
        policy = BackupPolicy(
            name=f"CI Stale Attempt {suffix}", is_enabled=True, scope_type="device", device_id=device.id, retry_count=2
        )
        db.add(policy)
        db.flush()
        run = BackupRun(device_id=device.id, policy_id=policy.id, status="pending", backup_type="mikrotik_multi")
        db.add(run)
        db.flush()
        job = DeviceJob(
            device_id=device.id,
            job_type="backup_mikrotik",
            status="pending",
            payload={"run_id": str(run.id), "formats": ["mikrotik_binary"]},
        )
        db.add(job)
        db.flush()
        db.add(MikrotikBackupJobSecret(job_id=job.id, encrypted_backup_password=encrypt_text("TEST-PASSWORD")))
        db.commit()
        return device.id, job.id, run.id, raw_secret


def main():
    device_id, job_id, run_id, raw_secret = seed()
    client = TestClient(app)
    headers = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw_secret}

    # Never delivered: the Agent cannot use the job yet.
    early = client.post(f"/api/v1/agents/mikrotik/jobs/{job_id}/backup-config", headers=headers, json={})
    assert early.status_code == 409, early.text

    heartbeat = client.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"inventory": {}})
    assert str(job_id) in {job["id"] for job in heartbeat.json()["jobs"]}
    config = client.post(f"/api/v1/agents/mikrotik/jobs/{job_id}/backup-config", headers=headers, json={})
    assert config.status_code == 200, config.text

    # Attempt 1 goes silent; maintenance re-queues the job for a retry.
    with SessionLocal() as db:
        retried, _ = recover_stale_jobs(db, utcnow() + timedelta(hours=1))
        db.commit()
        assert retried >= 1
        assert db.get(DeviceJob, job_id).status == "pending"

    # Attempt 1 finally reports failure: it must not fail the retry.
    late = client.post(
        f"/api/v1/agents/mikrotik/backup-jobs/{job_id}/complete",
        headers=headers,
        json={"status": "failed", "error": "RouterOS backup/upload failed", "result": {}},
    )
    assert late.status_code == 200, late.text
    assert late.json()["stale_attempt"] is True
    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        run = db.get(BackupRun, run_id)
        assert job.status == "pending" and job.completed_at is None
        assert run.status == "pending", "abandoned attempt must not finalize the run"
        assert db.get(MikrotikBackupJobSecret, job_id) is not None, "retry still needs its secret"
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.device_id == device_id,
                AuditEvent.event_type == "BACKUP_STALE_ATTEMPT_REPORT_IGNORED",
            )
        )
        assert event is not None and event.details["reported_status"] == "failed"
        job.not_before = None
        db.commit()

    start = client.post(
        f"/api/v1/agents/mikrotik/jobs/{job_id}/artifacts/start",
        headers=headers,
        json={"artifact_type": "mikrotik_binary", "size_bytes": 10},
    )
    assert start.status_code == 409, "abandoned attempt cannot start uploads on a re-queued job"

    # The retry is delivered again and owns the job from now on.
    heartbeat = client.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"inventory": {}})
    assert str(job_id) in {job["id"] for job in heartbeat.json()["jobs"]}
    config = client.post(f"/api/v1/agents/mikrotik/jobs/{job_id}/backup-config", headers=headers, json={})
    assert config.status_code == 200
    done = client.post(
        f"/api/v1/agents/mikrotik/backup-jobs/{job_id}/complete",
        headers=headers,
        json={"status": "failed", "error": "TEST retry failure", "result": {}},
    )
    assert done.status_code == 200 and "stale_attempt" not in done.json()
    with SessionLocal() as db:
        assert db.get(DeviceJob, job_id).status == "failed"
        assert db.get(BackupRun, run_id).status == "failed"
    print("MikroTik backup stale attempt report smoke passed")


if __name__ == "__main__":
    main()
