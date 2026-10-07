"""A retried backup attempt must be able to complete after a partial first attempt."""
import base64
import hashlib
import secrets
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_maintenance import recover_stale_jobs
from app.backup_models import BackupArtifact
from app.backup_storage import resolve_artifact_path
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_backup_models import MikrotikBackupJobSecret
from app.models import BackupPolicy, BackupRun, Customer, Device, utcnow
from app.secret_vault import encrypt_text

FIRST_BINARY = b"TEST-BINARY-ATTEMPT-1" * 50
SECOND_BINARY = b"TEST-BINARY-ATTEMPT-2" * 60
EXPORT = b"# TEST export\n/system identity set name=TEST-RETRY\n" * 20


def seed():
    suffix = uuid.uuid4().hex[:8]
    raw_secret = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        customer = Customer(name=f"CI Backup Retry {suffix}", code=f"BR{suffix[:6]}")
        db.add(customer)
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name=f"CI Backup Retry Router {suffix}",
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
            name=f"CI Backup Retry {suffix}",
            is_enabled=True,
            scope_type="device",
            device_id=device.id,
            retry_count=2,
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
            payload={"run_id": str(run.id), "formats": ["mikrotik_binary", "mikrotik_export"]},
        )
        db.add(job)
        db.flush()
        db.add(MikrotikBackupJobSecret(job_id=job.id, encrypted_backup_password=encrypt_text("TEST-PASSWORD")))
        db.commit()
        return device.id, job.id, run.id, raw_secret


def start(client, headers, job_id, artifact_type, size):
    return client.post(
        f"/api/v1/agents/mikrotik/jobs/{job_id}/artifacts/start",
        headers=headers,
        json={"artifact_type": artifact_type, "size_bytes": size},
    )


def send(client, headers, upload_id, payload, offset=0):
    response = client.post(
        f"/api/v1/agents/mikrotik/uploads/{upload_id}/chunk",
        headers=headers,
        json={"offset": offset, "data": base64.b64encode(payload).decode("ascii")},
    )
    assert response.status_code == 200, response.text
    return response.json()["next_offset"]


def upload(client, headers, job_id, artifact_type, payload):
    started = start(client, headers, job_id, artifact_type, len(payload))
    assert started.status_code == 200, started.text
    upload_id = started.json()["upload_id"]
    send(client, headers, upload_id, payload)
    finish = client.post(f"/api/v1/agents/mikrotik/uploads/{upload_id}/finish", headers=headers, json={})
    assert finish.status_code == 200, finish.text
    return finish.json()["artifact_id"]


def deliver(client, headers, job_id):
    heartbeat = client.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"inventory": {}})
    assert heartbeat.status_code == 200
    assert str(job_id) in {job["id"] for job in heartbeat.json()["jobs"]}


def main():
    device_id, job_id, run_id, raw_secret = seed()
    client = TestClient(app)
    headers = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw_secret}

    # Attempt 1: binary archived, export stalls half-way.
    deliver(client, headers, job_id)
    first_binary_id = upload(client, headers, job_id, "mikrotik_binary", FIRST_BINARY)
    duplicate = start(client, headers, job_id, "mikrotik_binary", len(FIRST_BINARY))
    assert duplicate.status_code == 409, "same attempt must not restart a completed artifact"
    stalled = start(client, headers, job_id, "mikrotik_export", len(EXPORT))
    assert stalled.status_code == 200
    send(client, headers, stalled.json()["upload_id"], EXPORT[:100])

    with SessionLocal() as db:
        retried, failed = recover_stale_jobs(db, utcnow() + timedelta(hours=1))
        db.commit()
        assert retried >= 1 and db.get(DeviceJob, job_id).status == "pending"
        job = db.get(DeviceJob, job_id)
        job.not_before = None
        db.commit()

    # Attempt 2 regenerates every format from scratch.
    deliver(client, headers, job_id)
    second_binary_id = upload(client, headers, job_id, "mikrotik_binary", SECOND_BINARY)
    upload(client, headers, job_id, "mikrotik_export", EXPORT)
    done = client.post(
        f"/api/v1/agents/mikrotik/backup-jobs/{job_id}/complete",
        headers=headers,
        json={"status": "success", "error": "", "result": {}},
    )
    assert done.status_code == 200, done.text

    with SessionLocal() as db:
        run = db.get(BackupRun, run_id)
        assert db.get(DeviceJob, job_id).status == "success"
        assert run.status == "success", run.error_message
        live = list(
            db.scalars(
                select(BackupArtifact).where(BackupArtifact.run_id == run_id, BackupArtifact.deleted_at.is_(None))
            )
        )
        assert sorted(a.artifact_type for a in live) == ["mikrotik_binary", "mikrotik_export"]
        assert run.size_bytes == len(SECOND_BINARY) + len(EXPORT)
        old = db.get(BackupArtifact, uuid.UUID(first_binary_id))
        assert old.deleted_at is not None, "previous attempt's artifact is superseded"
        new = db.get(BackupArtifact, uuid.UUID(second_binary_id))
        if old.storage_path != new.storage_path:
            assert not resolve_artifact_path(old.storage_path).exists()
        assert new.sha256 == hashlib.sha256(SECOND_BINARY).hexdigest()
        assert resolve_artifact_path(new.storage_path).read_bytes() == SECOND_BINARY
    print("MikroTik backup retry after partial attempt smoke passed")


if __name__ == "__main__":
    main()
