"""A self-update that never reports back must not block the Device forever."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app import mikrotik_agent as agent
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.device_job_maintenance import expire_delivered_jobs
from app.entrypoint import app
from app.mikrotik_agent_update import TARGET_AGENT_VERSION, UPDATE_JOB_TYPE, reconcile_agent_update_states
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password
from sqlalchemy import select

PASSWORD = "CI-Agent-Update-Expiry-2026"
SECRET = "ci-agent-update-expiry-secret"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        user = User(username=f"ci-upd-exp-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        customer = Customer(name=f"CI Update Expiry {suffix}", code=f"UE{suffix[:6]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name=f"CI Update Expiry {suffix}",
            firmware_version="7.20.7",
            status="online",
            last_seen=now,
            management_source="mikrotik_agent",
            inventory_data={
                "agent_version": TARGET_AGENT_VERSION,
                "agent_transport": "modern",
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


def queue(client, device_id):
    token = csrf_from(client.get(f"/devices/{device_id}/agent").text)
    response = client.post(f"/devices/{device_id}/agent/update", data={"csrf": token}, follow_redirects=False)
    assert response.status_code == 303
    with SessionLocal() as db:
        return db.scalar(
            select(DeviceJob.id)
            .where(DeviceJob.device_id == device_id, DeviceJob.job_type == UPDATE_JOB_TYPE)
            .order_by(DeviceJob.created_at.desc())
        )


def update_state(device_id):
    with SessionLocal() as db:
        return db.get(Device, device_id).inventory_data.get("agent_update_state")


def main():
    username, device_id = seed()
    client = TestClient(app)
    login = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)},
        follow_redirects=False,
    )
    assert login.status_code == 303
    headers = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": SECRET}

    job_id = queue(client, device_id)
    heartbeat = client.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"inventory": {}})
    assert str(job_id) in {job["id"] for job in heartbeat.json()["jobs"]}
    source = client.get(f"/api/v1/agents/mikrotik/self-update/{job_id}/source", headers=headers)
    assert source.status_code == 200
    with SessionLocal() as db:
        assert db.get(DeviceJob, job_id).status == "running"
    assert update_state(device_id) == "installing"

    # The router never reports back (e.g. reboot during update).
    later = utcnow() + timedelta(hours=1)
    expire_delivered_jobs(later)
    with SessionLocal() as db:
        assert db.get(DeviceJob, job_id).status == "running", "generic maintenance leaves running jobs to their domain"
    assert reconcile_agent_update_states(later) >= 1
    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        assert job.status == "failed" and "senza esito" in job.last_error
    assert update_state(device_id) == "expired"
    with SessionLocal() as db:
        assert db.scalar(
            select(AuditEvent).where(
                AuditEvent.device_id == device_id, AuditEvent.event_type == "MIKROTIK_AGENT_UPDATE_EXPIRED"
            )
        )
    fleet = client.get("/operations/agents?state=attention")
    assert "self-update scaduto" in fleet.text

    # A late success report must not resurrect the expired job.
    late = client.post(
        f"/api/v1/agents/mikrotik/self-update/{job_id}/complete?status=success&rolled_back=false",
        headers=headers,
    )
    assert late.status_code == 200 and late.json()["already_terminal"] is True
    assert update_state(device_id) == "expired"
    with SessionLocal() as db:
        assert db.get(DeviceJob, job_id).status == "failed"

    # The Device is no longer blocked: a new update can be queued.
    second = queue(client, device_id)
    assert second != job_id
    assert update_state(device_id) == "pending"
    print("MikroTik agent self-update expiry smoke passed")


if __name__ == "__main__":
    main()
