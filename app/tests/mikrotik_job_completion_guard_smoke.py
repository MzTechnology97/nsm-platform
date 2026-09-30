import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent as agent
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, utcnow

RAW_SECRET = "CI111-synthetic-device-secret"


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        customer = Customer(name=f"CI111 MikroTik Completion {suffix}", code=f"J11{suffix[:5]}")
        db.add(customer)
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI111 synthetic router",
            display_name="CI111 Completion Router",
            management_source="mikrotik_agent",
            status="online",
            primary_mac=f"02:11:{suffix[0:2]}:{suffix[2:4]}:{suffix[4:6]}:{suffix[6:8]}".upper(),
        )
        db.add(device)
        db.flush()
        db.add(
            DeviceAgentCredential(
                device_id=device.id,
                agent_type="mikrotik_agent",
                secret_hash=agent._secret_digest(RAW_SECRET),
                is_active=True,
            )
        )
        delivered = DeviceJob(
            device_id=device.id,
            job_type="diagnostic_ping",
            status="delivered",
            payload={"target": "198.51.100.20"},
            delivered_at=utcnow(),
            attempts=1,
        )
        pending = DeviceJob(
            device_id=device.id,
            job_type="snapshot_section",
            status="pending",
            payload={"section": "resources"},
        )
        already_failed = DeviceJob(
            device_id=device.id,
            job_type="firmware_readiness",
            status="failed",
            payload={},
            result={"original": True},
            last_error="Synthetic terminal failure",
            completed_at=utcnow(),
        )
        db.add_all([delivered, pending, already_failed])
        db.commit()
        return device.id, delivered.id, pending.id, already_failed.id


def main():
    client = TestClient(app)
    device_id, delivered_id, pending_id, already_failed_id = seed()
    headers = {
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": RAW_SECRET,
    }

    completion_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/api/v1/agents/mikrotik/jobs/{job_id}/complete"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert len(completion_routes) == 1
    assert completion_routes[0].name == "mikrotik_job_complete_idempotent"

    first = client.post(
        f"/api/v1/agents/mikrotik/jobs/{delivered_id}/complete",
        headers=headers,
        json={"status": "success", "result": {"probe": "first-result"}},
    )
    assert first.status_code == 200, first.text
    assert first.json() == {"status": "ok", "idempotent": False, "job_status": "success"}

    # Simulate a retry after RouterOS completed the POST but did not receive the
    # HTTP response. A conflicting retry must be a no-op, not rewrite history.
    duplicate = client.post(
        f"/api/v1/agents/mikrotik/jobs/{delivered_id}/complete",
        headers=headers,
        json={"status": "failed", "result": {"probe": "must-not-overwrite"}, "error": "retry"},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json() == {"status": "ok", "idempotent": True, "job_status": "success"}

    # An Agent must never be able to complete a job that the server has not
    # delivered yet.
    premature = client.post(
        f"/api/v1/agents/mikrotik/jobs/{pending_id}/complete",
        headers=headers,
        json={"status": "success", "result": {"probe": "premature"}},
    )
    assert premature.status_code == 409, premature.text

    # A late completion must not revive a job that maintenance/domain handling
    # has already finalized as failed.
    late = client.post(
        f"/api/v1/agents/mikrotik/jobs/{already_failed_id}/complete",
        headers=headers,
        json={"status": "success", "result": {"probe": "late"}},
    )
    assert late.status_code == 200, late.text
    assert late.json() == {"status": "ok", "idempotent": True, "job_status": "failed"}

    invalid = client.post(
        f"/api/v1/agents/mikrotik/jobs/{already_failed_id}/complete",
        headers=headers,
        json={"status": "unknown"},
    )
    assert invalid.status_code == 400

    with SessionLocal() as db:
        delivered = db.get(DeviceJob, delivered_id)
        assert delivered.status == "success"
        assert delivered.result == {"probe": "first-result"}
        assert delivered.last_error is None

        pending = db.get(DeviceJob, pending_id)
        assert pending.status == "pending"
        assert pending.completed_at is None

        already_failed = db.get(DeviceJob, already_failed_id)
        assert already_failed.status == "failed"
        assert already_failed.result == {"original": True}
        assert already_failed.last_error == "Synthetic terminal failure"

        events = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.device_id == device_id,
                    AuditEvent.event_type == "DEVICE_JOB_COMPLETED",
                )
            )
        )
        assert len(events) == 1
        assert events[0].details["job_id"] == str(delivered_id)
        assert events[0].details["status"] == "success"

    print("Modern MikroTik job completion idempotency smoke passed")


if __name__ == "__main__":
    main()
