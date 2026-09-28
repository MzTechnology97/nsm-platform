import hashlib
import re
import secrets
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.models import BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI32-Firmware-Staging-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    raw_secret = secrets.token_urlsafe(24)
    now = utcnow()
    with SessionLocal() as db:
        user = User(username=f"ci32-{suffix}", password_hash=hash_password(PASSWORD), display_name="CI32 Firmware Staging", role="admin", is_active=True)
        customer = Customer(name=f"CI32 Customer {suffix}", code=f"S32{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI32 Modern MikroTik",
            management_source="mikrotik_agent",
            status="online",
            firmware_version="7.20.0",
            recommended_firmware_version="7.21.1",
            firmware_status="update_available",
            inventory_data={"agent_transport": "modern", "agent_version": "0.20.0"},
        )
        db.add(device)
        db.flush()
        db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw_secret.encode()).hexdigest(), is_active=True, last_used_at=now))
        run = BackupRun(device_id=device.id, status="success", backup_type="mikrotik_multi", completed_at=now)
        db.add(run)
        db.flush()
        plan = FirmwareUpgradePlan(
            device_id=device.id,
            target_version="7.21.1",
            channel="stable",
            status="approved",
            backup_run_id=run.id,
            created_by=user.id,
            approved_by=user.id,
            approved_at=now,
            ready_at=now,
            precheck_data={"installed_version": "7.20.0", "target_version": "7.21.1", "agent_transport": "modern"},
        )
        db.add(plan)
        db.commit()
        return user.username, device.id, plan.id, raw_secret


def login(client, username):
    page = client.get("/login")
    token = csrf_from(page.text)
    response = client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False)
    assert response.status_code == 303


def main():
    major, minor, *_ = [int(part) for part in app.version.split('.')]
    assert (major, minor) >= (0, 32), app.version
    username, device_id, plan_id, raw_secret = seed()
    client = TestClient(app)
    login(client, username)

    page = client.get(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}")
    assert page.status_code == 200
    assert "DOWNLOAD 7.21.1" in page.text
    token = csrf_from(page.text)

    wrong = client.post(f"/devices/{device_id}/firmware-upgrade/{plan_id}/stage", data={"csrf": token, "confirmation": "DOWNLOAD WRONG"}, follow_redirects=False)
    assert wrong.status_code == 400

    page = client.get(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}")
    token = csrf_from(page.text)
    queued = client.post(f"/devices/{device_id}/firmware-upgrade/{plan_id}/stage", data={"csrf": token, "confirmation": "DOWNLOAD 7.21.1"}, follow_redirects=False)
    assert queued.status_code == 303

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        job = db.scalar(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "firmware_stage"))
        assert plan.status == "staging"
        assert job is not None and job.status == "pending"
        assert job.payload["target_version"] == "7.21.1"
        assert job.payload["download_only"] is True
        job_id = job.id

    headers = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw_secret}
    complete = client.post(
        f"/api/v1/agents/mikrotik/firmware-stage/{job_id}/complete",
        headers=headers,
        json={"status": "success", "error": "", "result": {"installed_version": "7.20.0", "latest_version": "7.21.1", "target_version": "7.21.1", "download_only": True}},
    )
    assert complete.status_code == 200

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        job = db.get(DeviceJob, job_id)
        assert plan.status == "staged"
        assert plan.precheck_data["staging"]["download_only"] is True
        assert job.status == "success"
        forbidden = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type.in_(["firmware_install", "routeros_upgrade", "firmware_reboot"]))))
        assert forbidden == []

    final_page = client.get(f"/devices/{device_id}/firmware-upgrade?plan={plan_id}")
    assert final_page.status_code == 200
    assert "ACTIVATE 7.21.1" in final_page.text
    assert "Attiva e riavvia MikroTik" in final_page.text
    with SessionLocal() as db:
        activation_jobs = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "firmware_activate")))
        assert activation_jobs == []
    print("Core 0.32 download-only staging smoke passed with explicit Core 0.37 activation gate")


if __name__ == "__main__":
    main()
