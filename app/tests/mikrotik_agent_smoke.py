import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, DeviceEnrollment, User
from app.security import hash_password

PASSWORD = "Strong-CI07-Password-2026"


def seed():
    with SessionLocal() as db:
        old = db.scalar(select(Customer).where(Customer.code == "CI07"))
        if old:
            db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci07admin"))
        if old_user:
            db.delete(old_user)
        db.commit()

        user = User(
            username="ci07admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI07 Admin",
            role="admin",
            is_active=True,
        )
        customer = Customer(name="CI07 MikroTik Lab", code="CI07")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="Nuovo dispositivo mikrotik",
            display_name="CI07 Router",
            management_source="mikrotik_agent",
            status="pending_enrollment",
        )
        db.add(device)
        db.flush()
        token, enrollment = core.create_enrollment(db, device, user)
        db.commit()
        return customer.id, device.id, enrollment.id, token


def main():
    customer_id, device_id, enrollment_id, token = seed()
    client = TestClient(app)

    bootstrap = client.get(
        "/api/v1/enrollment/mikrotik/bootstrap", params={"token": token}
    )
    assert bootstrap.status_code == 200
    assert "/api/v1/agents/mikrotik/enroll-legacy" in bootstrap.text
    assert "nsm-agent-heartbeat" in bootstrap.text
    assert token in bootstrap.text
    assert ":local nsmEscape do=" in bootstrap.text
    assert ":serialize" not in bootstrap.text
    assert ":deserialize" not in bootstrap.text

    enroll = client.post(
        "/api/v1/agents/mikrotik/enroll",
        json={
            "token": token,
            "inventory": {
                "identity": "CI07-CCR2004",
                "model": "CCR2004-1G-12S+2XS",
                "routeros_version": "7.12.1 (stable)",
                "architecture": "arm64",
                "serial_number": "CI07SERIAL",
                "software_id": "CI07-SOFTWARE-ID",
                "routerboot_version": "7.12.1",
                "primary_mac": "02:07:00:00:00:01",
                "uptime": "1d02:03:04",
                "cpu": "ARM64",
                "cpu_count": "4",
                "total_memory": "1024MiB",
                "free_memory": "800MiB",
                "agent_version": "0.7.0",
            },
        },
    )
    assert enroll.status_code == 200, enroll.text
    body = enroll.json()
    assert body["status"] == "ok"
    assert body["device_id"] == str(device_id)
    assert "agent_source" in body
    assert "/api/v1/agents/mikrotik/heartbeat" in body["agent_source"]
    assert ":local nsmEscape do=" in body["agent_source"]
    assert ":serialize" not in body["agent_source"]
    assert ":deserialize" not in body["agent_source"]
    match = re.search(r':local nsmSecret "([^"]+)"', body["agent_source"])
    assert match, "per-device secret not embedded in generated agent"
    raw_secret = match.group(1)

    second_enroll = client.post(
        "/api/v1/agents/mikrotik/enroll",
        json={"token": token, "inventory": {}},
    )
    assert second_enroll.status_code == 401

    with SessionLocal() as db:
        enrollment = db.get(DeviceEnrollment, enrollment_id)
        assert enrollment.status == "used"
        device = db.get(Device, device_id)
        assert device.device_identity == "CI07-CCR2004"
        assert device.model == "CCR2004-1G-12S+2XS"
        assert device.serial_number == "CI07SERIAL"
        assert device.primary_mac == "02:07:00:00:00:01"
        assert device.firmware_version == "7.12.1 (stable)"
        assert device.architecture == "arm64"
        assert device.routerboot_version == "7.12.1"
        assert device.status == "online"
        credential = db.scalar(
            select(DeviceAgentCredential).where(
                DeviceAgentCredential.device_id == device_id
            )
        )
        assert credential and credential.is_active
        assert raw_secret not in credential.secret_hash

        job = DeviceJob(
            device_id=device_id,
            job_type="inventory_refresh",
            payload={"reason": "CI smoke"},
        )
        db.add(job)
        db.commit()
        job_id = job.id

    headers = {
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": raw_secret,
    }
    heartbeat = client.post(
        "/api/v1/agents/mikrotik/heartbeat",
        headers=headers,
        json={
            "agent_version": "0.7.0",
            "inventory": {
                "identity": "CI07-CCR2004",
                "routeros_version": "7.20.2 (stable)",
                "uptime": "1d03:00:00",
            },
            "metrics": {"cpu_load": "12", "free_memory": "790MiB"},
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    heartbeat_data = heartbeat.json()
    assert heartbeat_data["status"] == "ok"
    assert len(heartbeat_data["jobs"]) == 1
    assert heartbeat_data["jobs"][0]["id"] == str(job_id)
    assert heartbeat_data["jobs"][0]["type"] == "inventory_refresh"

    bad_heartbeat = client.post(
        "/api/v1/agents/mikrotik/heartbeat",
        headers={**headers, "X-NSM-Device-Secret": "wrong-secret"},
        json={},
    )
    assert bad_heartbeat.status_code == 401

    complete = client.post(
        f"/api/v1/agents/mikrotik/jobs/{job_id}/complete",
        headers=headers,
        json={"status": "success", "result": {"refreshed": True}},
    )
    assert complete.status_code == 200

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        assert device.firmware_version == "7.20.2 (stable)"
        assert device.inventory_data["metrics"]["cpu_load"] == "12"
        credential = db.scalar(
            select(DeviceAgentCredential).where(
                DeviceAgentCredential.device_id == device_id
            )
        )
        assert credential.last_used_at is not None
        job = db.get(DeviceJob, job_id)
        assert job.status == "success"
        assert job.result["refreshed"] is True
        assert db.scalar(
            select(AuditEvent).where(
                AuditEvent.device_id == device_id,
                AuditEvent.event_type == "DEVICE_ENROLLED",
            )
        )
        assert db.scalar(
            select(AuditEvent).where(
                AuditEvent.device_id == device_id,
                AuditEvent.event_type == "FIRMWARE_CHANGED",
            )
        )

    print("Core 0.7 MikroTik agent smoke test passed with RouterOS 7.12 compatibility")


if __name__ == "__main__":
    main()
