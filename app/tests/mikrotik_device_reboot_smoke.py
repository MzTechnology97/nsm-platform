"""Operator-requested MikroTik reboot: gating, agent acknowledgement, verification."""
import hashlib
import re
import secrets
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_device_reboot import parse_uptime, verify_reboots
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Device-Reboot-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    assert parse_uptime("1w2d03:04:05") == 7 * 86400 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5
    assert parse_uptime("2d1h3m4s") == 2 * 86400 + 3600 + 3 * 60 + 4
    assert parse_uptime("00:05:12") == 312 and parse_uptime("45s") == 45 and parse_uptime("") is None and parse_uptime("n/a") is None

    source = mikrotik_agent._agent_source("http://nsm.example.test", uuid.UUID(int=42), "CI42-secret-not-for-production", False)
    handler = source[source.index('($nsmJobType = "device_reboot")'):source.index('($nsmJobType = "backup_mikrotik")')]
    assert handler.index("/device-reboot/") < handler.index("/system reboot"), "the agent acknowledges before rebooting"
    assert ":if ($nsmAckOk) do={" in handler and "check-certificate" not in handler

    suffix = uuid.uuid4().hex[:8]
    raw_secret = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        tech = User(username=f"ci-rb-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        operator = User(username=f"ci-rb-op-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True)
        customer = Customer(name=f"CI Reboot {suffix}", code=f"RB{suffix[:6]}")
        db.add_all([tech, operator, customer])
        db.flush()
        modern = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-RB-MODERN", status="online",
                        management_source="mikrotik_agent", inventory_data={"agent_transport": "modern", "agent_privilege_profile": "ops-v1", "uptime": "3d04:00:00"})
        legacy = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-RB-LEGACY", status="online",
                        inventory_data={"agent_transport": "legacy", "agent_privilege_profile": "legacy-read-v1"})
        db.add_all([modern, legacy])
        db.flush()
        db.add(DeviceAgentCredential(device_id=modern.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw_secret.encode()).hexdigest(), is_active=True))
        db.commit()
        modern_id, legacy_id = modern.id, legacy.id

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-rb-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{modern_id}/reboot")
    assert page.status_code == 200 and "Riavvio controllato" in page.text and f'href="/devices/{modern_id}/reboot">Riavvia<' in page.text
    token = csrf_from(page.text)
    assert "agent legacy ha solo permessi di lettura" in client.get(f"/devices/{legacy_id}/reboot").text

    response = client.post(f"/devices/{modern_id}/reboot", data={"csrf": token, "confirmation": "riavvia", "reason": "TEST porta bloccata"}, follow_redirects=True)
    assert "scrivi esattamente RIAVVIA" in response.text
    response = client.post(f"/devices/{modern_id}/reboot", data={"csrf": token, "confirmation": "RIAVVIA", "reason": "no"}, follow_redirects=True)
    assert "Indica il motivo" in response.text
    response = client.post(f"/devices/{modern_id}/reboot", data={"csrf": token, "confirmation": "RIAVVIA", "reason": "TEST porta bloccata"}, follow_redirects=True)
    assert "Riavvio in coda" in response.text
    again = client.post(f"/devices/{modern_id}/reboot", data={"csrf": token, "confirmation": "RIAVVIA", "reason": "TEST doppio click"}, follow_redirects=True)
    assert "Operazione in corso" in again.text, "a second reboot cannot be queued while one is pending"

    agent = TestClient(app)
    headers = {"X-NSM-Device-ID": str(modern_id), "X-NSM-Device-Secret": raw_secret}
    with SessionLocal() as db:
        job_id = db.scalar(select(DeviceJob.id).where(DeviceJob.device_id == modern_id, DeviceJob.job_type == "device_reboot"))
    # Not delivered yet: the agent must not reboot on a job it has not received.
    assert agent.post(f"/api/v1/agents/mikrotik/device-reboot/{job_id}/ack", headers=headers, json={"status": "accepted"}).status_code == 409
    beat = agent.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"inventory": {"uptime": "3d04:05:00"}})
    assert str(job_id) in {job["id"] for job in beat.json()["jobs"]}
    ack = agent.post(f"/api/v1/agents/mikrotik/device-reboot/{job_id}/ack", headers=headers, json={"status": "accepted", "result": {"uptime": "3d04:05:00"}})
    assert ack.status_code == 200, ack.text
    assert "in attesa del ritorno online" in client.get(f"/devices/{modern_id}/reboot").text

    # Same uptime trend (no reboot happened yet): nothing is verified.
    agent.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"inventory": {"uptime": "3d04:06:00"}})
    assert verify_reboots()["verified"] == 0
    with SessionLocal() as db:
        device = db.get(Device, modern_id)
        data = dict(device.inventory_data)
        data["device_reboot"] = {**data["device_reboot"], "accepted_at": (utcnow() - timedelta(minutes=2)).isoformat()}
        device.inventory_data = data
        db.commit()
    agent.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"inventory": {"uptime": "45s"}})
    assert verify_reboots()["verified"] == 1
    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        assert job.status == "success" and job.result["uptime_after"] == "45s"
        events = set(db.scalars(select(AuditEvent.event_type).where(AuditEvent.device_id == modern_id)))
        assert {"DEVICE_REBOOT_QUEUED", "DEVICE_REBOOT_ACCEPTED", "DEVICE_REBOOT_VERIFIED"} <= events
    assert "Riavvio verificato" in client.get(f"/devices/{modern_id}/reboot").text

    # A reboot that never comes back is reported after the verification window.
    client.post(f"/devices/{modern_id}/reboot", data={"csrf": token, "confirmation": "RIAVVIA", "reason": "TEST secondo riavvio"})
    with SessionLocal() as db:
        job2 = db.scalar(select(DeviceJob).where(DeviceJob.device_id == modern_id, DeviceJob.job_type == "device_reboot", DeviceJob.status == "pending"))
        job2.status = "delivered"
        db.commit()
        job2_id = job2.id
    agent.post(f"/api/v1/agents/mikrotik/device-reboot/{job2_id}/ack", headers=headers, json={"status": "accepted", "result": {"uptime": "1m"}})
    assert verify_reboots(utcnow() + timedelta(minutes=16))["failed"] == 1
    with SessionLocal() as db:
        assert db.get(DeviceJob, job2_id).status == "failed"

    # Operators read the page but cannot reboot.
    reader = TestClient(app)
    assert reader.post("/login", data={"username": f"ci-rb-op-{suffix}", "password": PASSWORD, "csrf": csrf_from(reader.get("/login").text)}, follow_redirects=False).status_code == 303
    view = reader.get(f"/devices/{modern_id}/reboot").text
    assert "Serve il permesso" in view and f'href="/devices/{modern_id}/reboot">Riavvia<' not in view
    assert reader.post(f"/devices/{modern_id}/reboot", data={"csrf": csrf_from(view), "confirmation": "RIAVVIA", "reason": "TEST operatore"}).status_code == 403
    print("MikroTik device reboot smoke passed")


if __name__ == "__main__":
    main()
