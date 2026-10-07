"""RouterBOOT workflow must recover when the Agent never runs a queued phase."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.device_job_maintenance import expire_pending_jobs
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.routerboot_lifecycle import REBOOT_JOB, STAGE_JOB, _write_state, verification_tick
from app.security import hash_password

PASSWORD = "CI-RouterBOOT-Recovery-2026"


def seed(status_state=None):
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        user = User(username=f"ci-rbrec-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        customer = Customer(name=f"CI RouterBOOT Recovery {suffix}", code=f"RB{suffix[:6]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name=f"CI RouterBOOT Recovery {suffix}",
            management_source="mikrotik_agent",
            status="online",
            firmware_version="7.22.0",
            routerboot_version="7.21.1",
            last_seen=now,
            inventory_data={
                "agent_transport": "modern",
                "agent_privilege_profile": "ops-v1",
                "firmware_readiness": {
                    "installed_version": "7.22.0",
                    "latest_version": "7.22.0",
                    "routerboard_current": "7.21.1",
                    "routerboard_upgrade": "7.22.0",
                    "checked_at": now.isoformat(),
                },
            },
        )
        db.add(device)
        db.flush()
        db.add(
            DeviceAgentCredential(
                device_id=device.id, agent_type="mikrotik_agent", secret_hash="4" * 64, is_active=True, last_used_at=now
            )
        )
        db.commit()
        return user.username, device.id


def queue_job(device_id, job_type, created_ago):
    created = utcnow() - created_ago
    with SessionLocal() as db:
        job = DeviceJob(
            device_id=device_id,
            job_type=job_type,
            payload={"target_version": "7.22.0"},
            created_at=created,
            expires_at=created + timedelta(minutes=10),
        )
        db.add(job)
        db.flush()
        device = db.get(Device, device_id)
        if job_type == STAGE_JOB:
            _write_state(device, status="staging", target_version="7.22.0", stage_job_id=str(job.id))
        else:
            _write_state(
                device,
                status="reboot_pending",
                target_version="7.22.0",
                reboot_job_id=str(job.id),
                reboot_queued_at=created.isoformat(),
            )
        db.commit()
        return job.id


def state(device_id):
    with SessionLocal() as db:
        return dict(db.get(Device, device_id).inventory_data["routerboot_lifecycle"])


def events(device_id, event_type):
    with SessionLocal() as db:
        return list(
            db.scalars(select(AuditEvent).where(AuditEvent.device_id == device_id, AuditEvent.event_type == event_type))
        )


def main():
    # 1) Flash job never delivered: state leaves "staging" and the GUI offers staging again.
    username, device_id = seed()
    queue_job(device_id, STAGE_JOB, timedelta(minutes=20))
    verification_tick()
    assert state(device_id)["status"] == "staging", "still pending: no recovery before expiry"
    assert expire_pending_jobs() >= 1
    stats = verification_tick()
    assert stats["failed"] >= 1
    current = state(device_id)
    assert current["status"] == "failed" and "Flash RouterBOOT non eseguito" in current["last_error"]
    assert events(device_id, "ROUTERBOOT_STAGE_EXPIRED")
    client = TestClient(app)
    page = client.get("/login")
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    assert client.post(
        "/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False
    ).status_code == 303
    workspace = client.get(f"/devices/{device_id}/routerboot")
    assert "ROUTERBOOT 7.22.0" in workspace.text, "staging must be offered again after recovery"

    # 2) Reboot job never delivered: back to "staged" (flash already done), reboot offered again.
    _, device_id = seed()
    queue_job(device_id, REBOOT_JOB, timedelta(minutes=12))
    assert expire_pending_jobs() >= 1
    stats = verification_tick()
    assert stats["reverted"] >= 1
    current = state(device_id)
    assert current["status"] == "staged" and current["reboot_job_id"] is None
    assert "nessun riavvio effettuato" in current["last_error"]
    assert events(device_id, "ROUTERBOOT_REBOOT_EXPIRED")

    # 3) Reboot accepted but no RouterBOOT reading ever comes back: fail after the verify timeout.
    _, device_id = seed()
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        data = dict(device.inventory_data)
        data["firmware_readiness"] = {"checked_at": utcnow().isoformat()}
        device.inventory_data = data
        _write_state(
            device,
            status="rebooting",
            target_version="7.22.0",
            accepted_at=(utcnow() - timedelta(minutes=30)).isoformat(),
        )
        db.commit()
    stats = verification_tick()
    assert stats["failed"] >= 1
    assert state(device_id)["status"] == "failed"
    assert "Nessuna lettura RouterBOOT" in state(device_id)["last_error"]
    print("RouterBOOT recovery smoke passed")


if __name__ == "__main__":
    main()
