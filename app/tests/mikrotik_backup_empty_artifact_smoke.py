"""An empty or still-growing RouterOS file must never become a successful backup."""
import hashlib
import secrets
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent as agent_module
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_backup_models import BackupUploadSession
from app.models import BackupRun, Customer, Device, utcnow


def seed():
    suffix = uuid.uuid4().hex[:8]
    raw_secret = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        customer = Customer(name=f"CI Empty Backup {suffix}", code=f"EB{suffix[:6]}")
        db.add(customer)
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name=f"CI Empty Backup Router {suffix}",
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
        run = BackupRun(device_id=device.id, status="pending", backup_type="mikrotik_multi")
        db.add(run)
        db.flush()
        job = DeviceJob(
            device_id=device.id,
            job_type="backup_mikrotik",
            status="delivered",
            delivered_at=utcnow(),
            payload={"run_id": str(run.id), "formats": ["mikrotik_binary", "mikrotik_export"]},
        )
        db.add(job)
        db.commit()
        return device.id, job.id, raw_secret


def check_server_rejects_empty_artifact():
    device_id, job_id, raw_secret = seed()
    client = TestClient(app)
    headers = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw_secret}
    for size in (0, "0"):
        response = client.post(
            f"/api/v1/agents/mikrotik/jobs/{job_id}/artifacts/start",
            headers=headers,
            json={"artifact_type": "mikrotik_binary", "size_bytes": size},
        )
        assert response.status_code == 400, response.text
        assert "vuoto" in response.json()["detail"]
    with SessionLocal() as db:
        sessions = list(db.scalars(select(BackupUploadSession).where(BackupUploadSession.job_id == job_id)))
        assert sessions == [], "an empty artifact must not open an upload session"
        assert db.get(DeviceJob, job_id).status == "delivered"

    accepted = client.post(
        f"/api/v1/agents/mikrotik/jobs/{job_id}/artifacts/start",
        headers=headers,
        json={"artifact_type": "mikrotik_binary", "size_bytes": 1},
    )
    assert accepted.status_code == 200, accepted.text


def check_agent_waits_for_settled_non_empty_file():
    source = agent_module._agent_source(
        "https://nsm.example.test", uuid.UUID(int=7), "TEST-AGENT-SECRET", True
    )
    backup = source[source.index(':if ($nsmJobType = "backup_mikrotik") do={') :]
    settle = backup.index(":while (($nsmWaitTicks < 60) && (($nsmFileSize = 0) || ($nsmFileSize != $nsmPrevSize))) do={")
    empty_guard = backup.index(':if ($nsmFileSize = 0) do={ :error "NSM backup file is empty" }')
    settled_guard = backup.index(':if ($nsmFileSize != $nsmPrevSize) do={ :error "NSM backup file size did not settle" }')
    start = backup.index("/artifacts/start")
    assert settle < empty_guard < settled_guard < start, "file must settle before the upload starts"
    assert ":set nsmPrevSize $nsmFileSize" in backup
    assert ":local nsmFileId [/file find where name=$nsmFileName]" not in backup, "single-shot size read survived"
    # The read loop hardening from earlier fixes must still be present.
    assert "/file read file=$nsmFileName offset=$nsmOffset chunk-size=$nsmChunkSize as-value" in backup


def main():
    check_server_rejects_empty_artifact()
    check_agent_waits_for_settled_non_empty_file()
    print("MikroTik empty/unsettled backup artifact smoke passed")


if __name__ == "__main__":
    main()
