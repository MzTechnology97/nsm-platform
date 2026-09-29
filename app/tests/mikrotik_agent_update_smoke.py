import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent as agent
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_agent_update import TARGET_AGENT_VERSION, UPDATE_JOB_TYPE
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI49-Agent-Update-2026"
SECRET = "ci49-device-secret"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "csrf token missing"
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        user = User(
            username=f"ci49-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI49 Agent Update",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI49 Customer {suffix}", code=f"U49{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI49 Modern Router",
            display_name="CI49 Modern Router",
            device_identity="CI49-RTR",
            model="RB5009UG+S+",
            firmware_version="7.20.7",
            status="online",
            last_seen=now,
            management_source="mikrotik_agent",
            inventory_data={
                "agent_version": TARGET_AGENT_VERSION,
                "agent_transport": "modern",
                "agent_update_protocol": "source-v1",
                "agent_expected_version": TARGET_AGENT_VERSION,
                "agent_expected_source_sha512": "a" * 128,
                "agent_source_sha512": "b" * 128,
                "agent_source_drift": True,
                "last_heartbeat_at": now.isoformat(),
            },
        )
        db.add(device)
        db.flush()
        db.add(
            DeviceAgentCredential(
                device_id=device.id,
                agent_type="mikrotik_agent",
                secret_hash=agent._secret_digest(SECRET),
                is_active=True,
                created_at=now,
                last_used_at=now,
            )
        )
        db.commit()
        return user.username, device.id


def login(client: TestClient, username: str):
    page = client.get("/login")
    token = _csrf(page.text)
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def auth_headers(device_id):
    return {
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": SECRET,
    }


def queue_update(client, device_id):
    page = client.get(f"/devices/{device_id}/agent")
    assert page.status_code == 200
    token = _csrf(page.text)
    response = client.post(
        f"/devices/{device_id}/agent/update",
        data={"csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(DeviceJob)
                .where(DeviceJob.device_id == device_id, DeviceJob.job_type == UPDATE_JOB_TYPE)
                .order_by(DeviceJob.created_at.desc())
            )
        )
        assert jobs
        return jobs[0].id


def deliver_job(client, device_id, source_hash=None):
    inventory = {
        "identity": "CI49-RTR",
        "model": "RB5009UG+S+",
        "routeros_version": "7.20.7",
        "agent_version": TARGET_AGENT_VERSION,
    }
    if source_hash:
        inventory["agent_source_sha512"] = source_hash
    response = client.post(
        "/api/v1/agents/mikrotik/heartbeat",
        headers=auth_headers(device_id),
        json={"inventory": inventory, "metrics": {}, "agent_version": TARGET_AGENT_VERSION},
    )
    assert response.status_code == 200, response.text
    return response


def main():
    username, device_id = seed()
    client = TestClient(app)
    login(client, username)

    # Drift is visible in the existing fleet without adding another competing
    # fleet route.
    fleet = client.get("/operations/agents?state=attention")
    assert fleet.status_code == 200
    assert "Agent source drift" in fleet.text

    # First attempt: deliver NSM-generated source and simulate RouterOS rollback.
    first_job = queue_update(client, device_id)
    heartbeat = deliver_job(client, device_id)
    delivered = [item for item in heartbeat.json()["jobs"] if item["id"] == str(first_job)]
    assert len(delivered) == 1
    assert delivered[0]["type"] == UPDATE_JOB_TYPE

    source = client.get(
        f"/api/v1/agents/mikrotik/self-update/{first_job}/source",
        headers=auth_headers(device_id),
    )
    assert source.status_code == 200, source.text
    assert source.headers["x-nsm-source-sha512"]
    for marker in (
        "nsm-agent-heartbeat-prev",
        "previous-known-good",
        "Agent update failed; previous-known-good restored",
        "/self-update/",
        'transform=sha512',
    ):
        assert marker in source.text, marker
    # The server never accepts source text from the queue payload.
    with SessionLocal() as db:
        job = db.get(DeviceJob, first_job)
        assert "source" not in (job.payload or {})
        assert job.status == "running"

    rollback = client.post(
        f"/api/v1/agents/mikrotik/self-update/{first_job}/complete?status=failed&rolled_back=true",
        headers=auth_headers(device_id),
    )
    assert rollback.status_code == 200
    assert rollback.json()["state"] == "rolled_back"
    with SessionLocal() as db:
        job = db.get(DeviceJob, first_job)
        device = db.get(Device, device_id)
        assert job.status == "failed"
        assert job.result["rolled_back"] is True
        assert device.inventory_data["agent_update_state"] == "rolled_back"
        # Simulate drift again to exercise the successful repair path.
        data = dict(device.inventory_data or {})
        data["agent_expected_source_sha512"] = "c" * 128
        data["agent_source_sha512"] = "d" * 128
        data["agent_source_drift"] = True
        device.inventory_data = data
        db.commit()

    second_job = queue_update(client, device_id)
    heartbeat = deliver_job(client, device_id)
    assert any(item["id"] == str(second_job) for item in heartbeat.json()["jobs"])
    source = client.get(
        f"/api/v1/agents/mikrotik/self-update/{second_job}/source",
        headers=auth_headers(device_id),
    )
    assert source.status_code == 200
    digest = source.headers["x-nsm-source-sha512"]

    # The new script's first heartbeat is the verification gate.
    verified_heartbeat = deliver_job(client, device_id, digest)
    assert verified_heartbeat.status_code == 200
    complete = client.post(
        f"/api/v1/agents/mikrotik/self-update/{second_job}/complete?status=success&rolled_back=false",
        headers=auth_headers(device_id),
    )
    assert complete.status_code == 200
    assert complete.json()["state"] == "verified"
    with SessionLocal() as db:
        job = db.get(DeviceJob, second_job)
        device = db.get(Device, device_id)
        assert job.status == "success"
        assert device.inventory_data["agent_update_state"] == "verified"
        assert device.inventory_data["agent_source_drift"] is False
        assert device.inventory_data["agent_update_verified_at"]

    # Legacy remains fail-closed/reinstall-only; no write privilege is added.
    legacy = Device(
        vendor="mikrotik",
        display_name="CI49 Legacy",
        firmware_version="7.12.1",
        inventory_data={"agent_transport": "legacy", "agent_version": "0.49.0-legacy"},
    )
    from app.mikrotik_agent_update import agent_update_status
    legacy_status = agent_update_status(legacy)
    assert legacy_status["self_update_capable"] is False
    assert legacy_status["protocol"] == "reinstall-only"

    print("Core 0.49 MikroTik agent self-update/rollback smoke passed")


if __name__ == "__main__":
    main()
