import base64
import hashlib
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceJob
from app.backup_models import BackupArtifact, BackupPolicySettings
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_backup_models import BackupUploadSession, MikrotikBackupJobSecret
from app.models import BackupPolicy, BackupRun, Customer, Device, User
from app.secret_vault import decrypt_text
from app.security import hash_password

PASSWORD = "Strong-CI08-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    with SessionLocal() as db:
        old = db.scalar(select(Customer).where(Customer.code == "CI08"))
        if old:
            db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci08admin"))
        if old_user:
            db.delete(old_user)
        db.commit()
        user = User(
            username="ci08admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI08 Admin",
            role="admin",
            is_active=True,
        )
        customer = Customer(name="CI08 Backup Lab", code="CI08")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI08 Router",
            display_name="CI08 Router",
            management_source="mikrotik_agent",
            status="pending_enrollment",
        )
        db.add(device)
        db.flush()
        token, _ = core.create_enrollment(db, device, user)
        policy = BackupPolicy(
            name="CI08 Device Backup",
            is_enabled=True,
            scope_type="device",
            device_id=device.id,
            schedule_cron="0 3 * * *",
            binary_backup=True,
            text_export=True,
            pre_firmware_backup=True,
            verify_hash=True,
        )
        db.add(policy)
        db.flush()
        db.add(
            BackupPolicySettings(
                policy_id=policy.id,
                schedule_kind="daily",
                schedule_time="03:00",
                options={
                    "mikrotik_binary": True,
                    "mikrotik_export": True,
                    "pre_firmware": True,
                    "verify_hash": True,
                },
            )
        )
        db.commit()
        return device.id, token


def upload(client, headers, job_id, artifact_type, payload: bytes):
    start = client.post(
        f"/api/v1/agents/mikrotik/jobs/{job_id}/artifacts/start",
        headers=headers,
        json={"artifact_type": artifact_type, "size_bytes": len(payload)},
    )
    assert start.status_code == 200, start.text
    upload_id = start.json()["upload_id"]
    offset = 0
    chunk_size = 24576
    while offset < len(payload):
        chunk = payload[offset : offset + chunk_size]
        response = client.post(
            f"/api/v1/agents/mikrotik/uploads/{upload_id}/chunk",
            headers=headers,
            json={"offset": offset, "data": base64.b64encode(chunk).decode("ascii")},
        )
        assert response.status_code == 200, response.text
        offset = response.json()["next_offset"]
    finish = client.post(
        f"/api/v1/agents/mikrotik/uploads/{upload_id}/finish",
        headers=headers,
        json={},
    )
    assert finish.status_code == 200, finish.text
    assert finish.json()["size_bytes"] == len(payload)
    assert finish.json()["sha256"] == hashlib.sha256(payload).hexdigest()
    return finish.json()["artifact_id"]


def main():
    route = app.url_path_for(
        "backup_aware_job_complete",
        job_id="00000000-0000-0000-0000-000000000001",
    )
    assert str(route) == "/api/v1/agents/mikrotik/backup-jobs/00000000-0000-0000-0000-000000000001/complete"

    device_id, token = seed()
    client = TestClient(app)

    enroll = client.post(
        "/api/v1/agents/mikrotik/enroll",
        json={
            "token": token,
            "inventory": {
                "identity": "CI08-CCR",
                "model": "CCR2004-1G-12S+2XS",
                "routeros_version": "7.20.2 (stable)",
                "architecture": "arm64",
                "serial_number": "CI08SERIAL",
                "primary_mac": "02:08:00:00:00:01",
            },
        },
    )
    assert enroll.status_code == 200, enroll.text
    match = re.search(r':local nsmSecret "([^"]+)"', enroll.json()["agent_source"])
    assert match
    device_secret = match.group(1)
    headers = {
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": device_secret,
    }

    login = client.get("/login")
    csrf = csrf_from(login.text)
    logged = client.post(
        "/login",
        data={"username": "ci08admin", "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert logged.status_code == 303
    detail = client.get(f"/devices/{device_id}")
    csrf = csrf_from(detail.text)
    queued = client.post(
        f"/devices/{device_id}/backup-now",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert queued.status_code == 303, queued.text

    with SessionLocal() as db:
        job = db.scalar(
            select(DeviceJob).where(
                DeviceJob.device_id == device_id,
                DeviceJob.job_type == "backup_mikrotik",
            )
        )
        assert job and job.status == "pending"
        job_id = job.id
        run_id = uuid.UUID(str(job.payload["run_id"]))
        stored_secret = db.get(MikrotikBackupJobSecret, job.id)
        assert stored_secret
        assert "CI08" not in stored_secret.encrypted_backup_password

    heartbeat = client.post(
        "/api/v1/agents/mikrotik/heartbeat",
        headers=headers,
        json={"agent_version": "0.8.0", "inventory": {"identity": "CI08-CCR"}, "metrics": {}},
    )
    assert heartbeat.status_code == 200
    jobs = heartbeat.json()["jobs"]
    assert len(jobs) == 1 and jobs[0]["type"] == "backup_mikrotik"
    assert jobs[0]["id"] == str(job_id)

    config = client.post(
        f"/api/v1/agents/mikrotik/jobs/{job_id}/backup-config",
        headers=headers,
        json={},
    )
    assert config.status_code == 200, config.text
    config_json = config.json()
    assert set(config_json["formats"]) == {"mikrotik_binary", "mikrotik_export"}
    assert len(config_json["backup_password"]) >= 20
    assert config_json["chunk_size"] <= 32768
    with SessionLocal() as db:
        stored_secret = db.get(MikrotikBackupJobSecret, job_id)
        assert decrypt_text(stored_secret.encrypted_backup_password) == config_json["backup_password"]
        assert stored_secret.encrypted_backup_password != config_json["backup_password"]

    binary_payload = bytes((i % 251 for i in range(90000)))
    export_payload = ("# CI08 RouterOS export\n/interface bridge print\n" * 1800).encode()
    binary_artifact_id = upload(client, headers, job_id, "mikrotik_binary", binary_payload)
    export_artifact_id = upload(client, headers, job_id, "mikrotik_export", export_payload)

    complete = client.post(
        f"/api/v1/agents/mikrotik/backup-jobs/{job_id}/complete",
        headers=headers,
        json={"status": "success", "result": {"agent_version": "0.8.0"}},
    )
    assert complete.status_code == 200, complete.text

    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        run = db.get(BackupRun, run_id)
        assert job.status == "success"
        uploads = list(db.scalars(select(BackupUploadSession).where(BackupUploadSession.job_id == job.id)))
        assert run.status == "success", {
            "status": run.status,
            "error": run.error_message,
            "job_status": job.status,
            "job_error": job.last_error,
            "expected": job.payload.get("formats"),
            "uploads": [
                (item.artifact_type, item.status, item.received_size, item.expected_size)
                for item in uploads
            ],
        }
        artifacts = list(db.scalars(select(BackupArtifact).where(BackupArtifact.run_id == run.id)))
        assert {a.artifact_type for a in artifacts} == {"mikrotik_binary", "mikrotik_export"}
        assert sum(a.size_bytes for a in artifacts) == len(binary_payload) + len(export_payload)

    binary_download = client.get(
        f"/operations/backups/artifacts/{binary_artifact_id}/download"
    )
    assert binary_download.status_code == 200
    assert binary_download.content == binary_payload
    export_download = client.get(
        f"/operations/backups/artifacts/{export_artifact_id}/download"
    )
    assert export_download.status_code == 200
    assert export_download.content == export_payload

    print("Core 0.8 chunked MikroTik backup smoke test passed")


if __name__ == "__main__":
    main()
