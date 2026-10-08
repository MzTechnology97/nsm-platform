import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceJob
from app.backup_capabilities import capability_for_device
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User
from app.security import hash_password

PASSWORD = "Strong-CI29-Password-2026"


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"ci29-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI29 Legacy Jobs",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI29 RouterOS 7.12 {suffix}", code=f"L29{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="RouterOS 7.12.1 legacy jobs",
            display_name="CI29 Legacy Router",
            management_source="mikrotik_agent",
            status="pending_enrollment",
        )
        db.add(device)
        db.flush()
        token, _ = core.create_enrollment(db, device, user)
        db.commit()
        return device.id, token


def main():
    client = TestClient(app, base_url="https://nsm.example.net")
    device_id, token = seed()

    bootstrap = client.get("/api/v1/enrollment/mikrotik/bootstrap", params={"token": token})
    assert bootstrap.status_code == 200, bootstrap.text
    assert "/api/v1/agents/mikrotik/enroll-legacy" in bootstrap.text
    assert ":serialize" not in bootstrap.text
    assert ":deserialize" not in bootstrap.text

    enroll = client.post(
        "/api/v1/agents/mikrotik/enroll-legacy",
        json={
            "token": token,
            "inventory": {
                "identity": "CI29-WAP-R",
                "model": "wAP R",
                "routeros_version": "7.12.1 (stable)",
                "architecture": "mipsbe",
                "serial_number": f"CI29{uuid.uuid4().hex[:8]}",
                "primary_mac": "02:29:00:00:00:12",
                "agent_version": "0.20.0-legacy",
            },
        },
    )
    assert enroll.status_code == 200, enroll.text
    source = enroll.text
    assert "/api/v1/agents/mikrotik/heartbeat-legacy" in source
    assert "/api/v1/agents/mikrotik/legacy/jobs/next" in source
    assert "/complete?status=" in source
    assert ":serialize" not in source
    assert ":deserialize" not in source
    assert ":execute script=" not in source

    secret_match = re.search(r':local nsmSecret "([^"]+)"', source)
    id_match = re.search(r':local nsmDeviceId "([^"]+)"', source)
    assert secret_match and id_match
    assert id_match.group(1) == str(device_id)
    headers = {
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": secret_match.group(1),
    }

    heartbeat = client.post(
        "/api/v1/agents/mikrotik/heartbeat-legacy",
        headers=headers,
        json={
            "agent_version": "0.20.0-legacy",
            "inventory": {
                "identity": "CI29-WAP-R",
                "routeros_version": "7.12.1 (stable)",
            },
            "metrics": {"cpu_load": "9", "free_memory": "31MiB"},
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["jobs"] == []

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        capability = capability_for_device(db, device)
        assert capability.status == "legacy_backup_pending"
        assert capability.executable is False
        ping = DeviceJob(
            device_id=device_id,
            job_type="diagnostic_ping",
            payload={"target": "1.1.1.1", "source": ""},
        )
        backup = DeviceJob(
            device_id=device_id,
            job_type="backup_mikrotik",
            payload={"reason": "must fail closed on legacy transport"},
        )
        db.add_all([ping, backup])
        db.commit()
        ping_id = ping.id
        backup_id = backup.id

    wrong = client.get(
        "/api/v1/agents/mikrotik/legacy/jobs/next",
        headers={**headers, "X-NSM-Device-Secret": "wrong"},
    )
    assert wrong.status_code == 401

    next_job = client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers)
    assert next_job.status_code == 200, next_job.text
    assert next_job.text == f"{ping_id}|diagnostic_ping|1.1.1.1|"

    with SessionLocal() as db:
        ping = db.get(DeviceJob, ping_id)
        backup = db.get(DeviceJob, backup_id)
        assert ping.status == "delivered" and ping.attempts == 1
        assert backup.status == "failed"
        assert "Backup non eseguibile con questo agent legacy" in (backup.last_error or "")

    output = "sent=10 received=10 packet-loss=0% avg-rtt=4ms"
    complete = client.post(
        f"/api/v1/agents/mikrotik/legacy/jobs/{ping_id}/complete?status=success",
        headers={**headers, "Content-Type": "text/plain"},
        content=output,
    )
    assert complete.status_code == 200, complete.text

    with SessionLocal() as db:
        ping = db.get(DeviceJob, ping_id)
        assert ping.status == "success"
        assert ping.result["legacy_transport"] is True
        assert ping.result["output"] == output
        event = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.device_id == device_id, AuditEvent.event_type == "DEVICE_JOB_COMPLETED")
            .order_by(AuditEvent.timestamp.desc())
        )
        assert event is not None

        dhcp = DeviceJob(
            device_id=device_id,
            job_type="diagnostic_dhcp_lookup",
            payload={"query": "AA:BB:CC:DD:EE:FF", "lookup_type": "mac"},
        )
        db.add(dhcp)
        db.commit()
        dhcp_id = dhcp.id

    dhcp_next = client.get("/api/v1/agents/mikrotik/legacy/jobs/next", headers=headers)
    assert dhcp_next.status_code == 200
    assert dhcp_next.text == f"{dhcp_id}|diagnostic_dhcp_lookup|AA:BB:CC:DD:EE:FF|mac"

    print("Core 0.29 RouterOS legacy job transport smoke passed")


if __name__ == "__main__":
    main()
