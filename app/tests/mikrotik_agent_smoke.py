import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceAgentCredential
from app.agent_service import mark_stale_agents_offline, secret_hash
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "Strong-CI-Agent-Password-2026"


def setup_records():
    with SessionLocal() as db:
        user = User(
            username="ci-agent-admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI Agent Admin",
            role="admin",
            is_active=True,
        )
        customer = Customer(name="CI MikroTik Customer", code="CIMTK")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="Nuovo dispositivo mikrotik",
            display_name="CI MikroTik Router",
            management_source="mikrotik_agent",
            status="pending_enrollment",
        )
        db.add(device)
        db.flush()
        raw_token, _ = core.create_enrollment(
            db, device, user, source="mikrotik_agent_v1"
        )
        db.commit()
        return device.id, raw_token


def main():
    device_id, raw_token = setup_records()
    client = TestClient(app)

    response = client.get(
        "/api/v1/agent/mikrotik/bootstrap", params={"token": raw_token}
    )
    assert response.status_code == 200
    script = response.text
    assert 'name="nsm-agent"' in script
    assert '/api/v1/agent/mikrotik/heartbeat' in script
    assert '/api/v1/agent/mikrotik/complete' in script
    secret_match = re.search(r':local nsmSecret "([^"]+)"', script)
    assert secret_match, "Derived device secret missing from generated RouterOS script"
    device_secret = secret_match.group(1)
    assert raw_token not in device_secret

    complete_body = "\n".join(
        [
            f"token={raw_token}",
            "identity=CI-RouterOS",
            "model=RB5009UG+S+",
            "version=7.20.1",
            "architecture=arm64",
            "serial=CI123456",
            "software_id=C1D2-E3F4",
            "routerboot=7.20.1",
        ]
    )
    response = client.post(
        "/api/v1/agent/mikrotik/complete",
        content=complete_body,
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 200, response.text

    with SessionLocal() as db:
        credential = db.get(DeviceAgentCredential, device_id)
        device = db.get(Device, device_id)
        assert credential is not None
        assert credential.secret_hash == secret_hash(device_secret)
        assert credential.secret_hash != device_secret
        assert credential.last_heartbeat_at is not None
        assert device.status == "online"
        assert device.device_identity == "CI-RouterOS"
        assert device.model == "RB5009UG+S+"
        assert device.firmware_version == "7.20.1"
        assert device.serial_number == "CI123456"

    replay = client.post(
        "/api/v1/agent/mikrotik/complete",
        content=complete_body,
        headers={"Content-Type": "text/plain"},
    )
    assert replay.status_code == 401

    bad_heartbeat = "\n".join(
        [
            f"device_id={device_id}",
            "device_secret=wrong-secret",
            "identity=CI-RouterOS",
            "version=7.20.1",
        ]
    )
    response = client.post(
        "/api/v1/agent/mikrotik/heartbeat",
        content=bad_heartbeat,
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 401

    heartbeat = "\n".join(
        [
            f"device_id={device_id}",
            f"device_secret={device_secret}",
            "identity=CI-RouterOS",
            "model=RB5009UG+S+",
            "version=7.20.1",
            "architecture=arm64",
            "serial=CI123456",
            "routerboot=7.20.1",
            "uptime=1d02:03:04",
            "cpu_load=17",
            "free_memory=123456789",
            "total_memory=1073741824",
        ]
    )
    response = client.post(
        "/api/v1/agent/mikrotik/heartbeat",
        content=heartbeat,
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 200, response.text

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        credential = db.get(DeviceAgentCredential, device_id)
        health = (device.inventory_data or {}).get("health", {})
        assert device.status == "online"
        assert device.last_seen is not None
        assert credential.last_heartbeat_at is not None
        assert health["uptime"] == "1d02:03:04"
        assert health["cpu_load"] == 17
        assert health["free_memory"] == 123456789
        assert health["total_memory"] == 1073741824

        from datetime import timedelta

        credential.last_heartbeat_at = utcnow() - timedelta(hours=1)
        device.status = "online"
        db.commit()

    with SessionLocal() as db:
        changed = mark_stale_agents_offline(db)
        assert changed == 1

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        assert device.status == "offline"
        assert db.scalar(
            select(AuditEvent).where(
                AuditEvent.device_id == device_id,
                AuditEvent.event_type == "DEVICE_OFFLINE",
            )
        )

    response = client.post(
        "/api/v1/agent/mikrotik/heartbeat",
        content=heartbeat,
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        assert device.status == "online"
        assert db.scalar(
            select(AuditEvent).where(
                AuditEvent.device_id == device_id,
                AuditEvent.event_type == "DEVICE_ONLINE",
            )
        )

    print("Core 0.6 MikroTik agent smoke test passed")


if __name__ == "__main__":
    main()
