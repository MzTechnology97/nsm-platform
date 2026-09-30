from datetime import timedelta
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent as agent_core
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.device_job_maintenance import EXPIRED_DELIVERED_ERROR
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, utcnow

SECRET = "ci-terminal-job-secret"
MODERN_COMPLETE_PATH = "/api/v1/agents/mikrotik/jobs/{job_id}/complete"
LEGACY_COMPLETE_PATH = "/api/v1/agents/mikrotik/legacy/jobs/{job_id}/complete"


def _completion_events(db, device_id):
    return list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.device_id == device_id,
                AuditEvent.event_type == "DEVICE_JOB_COMPLETED",
            )
        )
    )


def _post_route_names(path: str) -> list[str | None]:
    return [
        getattr(route, "name", None)
        for route in app.router.routes
        if getattr(route, "path", None) == path
        and "POST" in (getattr(route, "methods", set()) or set())
    ]


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow().replace(microsecond=0)
    with SessionLocal() as db:
        customer = Customer(
            name=f"CI terminal completion {suffix}",
            code=f"TC{suffix[:6]}",
        )
        db.add(customer)
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI terminal completion router",
            display_name="CI terminal completion router",
            management_source="mikrotik_agent",
            status="online",
        )
        db.add(device)
        db.flush()
        db.add(
            DeviceAgentCredential(
                device_id=device.id,
                agent_type="mikrotik_agent",
                secret_hash=agent_core._secret_digest(SECRET),
                is_active=True,
            )
        )

        modern = DeviceJob(
            device_id=device.id,
            job_type="inventory_refresh",
            status="delivered",
            payload={"reason": "synthetic CI completion"},
            delivered_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        legacy = DeviceJob(
            device_id=device.id,
            job_type="diagnostic_ping",
            status="delivered",
            payload={"target": "192.0.2.10", "source": ""},
            delivered_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        expired = DeviceJob(
            device_id=device.id,
            job_type="diagnostic_logs",
            status="failed",
            payload={},
            result={"maintenance": True},
            delivered_at=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=1),
            expires_at=now - timedelta(minutes=2),
            last_error=EXPIRED_DELIVERED_ERROR,
        )
        db.add_all([modern, legacy, expired])
        db.commit()
        return device.id, modern.id, legacy.id, expired.id


def main():
    modern_routes = _post_route_names(MODERN_COMPLETE_PATH)
    legacy_routes = _post_route_names(LEGACY_COMPLETE_PATH)
    assert modern_routes and modern_routes[0] == "guarded_mikrotik_job_complete", modern_routes
    assert legacy_routes and legacy_routes[0] == "guarded_mikrotik_legacy_job_complete", legacy_routes

    device_id, modern_id, legacy_id, expired_id = seed()
    headers = {
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": SECRET,
    }
    client = TestClient(app)

    modern_complete = client.post(
        f"/api/v1/agents/mikrotik/jobs/{modern_id}/complete",
        headers=headers,
        json={"status": "success", "result": {"refreshed": True}},
    )
    assert modern_complete.status_code == 200, modern_complete.text
    assert modern_complete.json() == {"status": "ok"}, modern_complete.text

    with SessionLocal() as db:
        modern = db.get(DeviceJob, modern_id)
        first_completed_at = modern.completed_at
        assert modern.status == "success"
        assert modern.result == {"refreshed": True}
        assert modern.last_error is None
        event_count = len(_completion_events(db, device_id))

    duplicate = client.post(
        f"/api/v1/agents/mikrotik/jobs/{modern_id}/complete",
        headers=headers,
        json={
            "status": "failed",
            "error": "late duplicate must not replace success",
            "result": {"refreshed": False},
        },
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json() == {
        "status": "ok",
        "already_terminal": True,
        "job_status": "success",
    }, {"response": duplicate.json(), "routes": modern_routes}

    with SessionLocal() as db:
        modern = db.get(DeviceJob, modern_id)
        assert modern.status == "success"
        assert modern.result == {"refreshed": True}
        assert modern.last_error is None
        assert modern.completed_at == first_completed_at
        assert len(_completion_events(db, device_id)) == event_count

    # A job failed by expiry maintenance must never be resurrected by a late
    # success report from an Agent that was offline or delayed.
    with SessionLocal() as db:
        expired = db.get(DeviceJob, expired_id)
        expired_completed_at = expired.completed_at
        expired_result = dict(expired.result)

    late_success = client.post(
        f"/api/v1/agents/mikrotik/jobs/{expired_id}/complete",
        headers=headers,
        json={"status": "success", "result": {"late": True}},
    )
    assert late_success.status_code == 200, late_success.text
    assert late_success.json()["already_terminal"] is True, late_success.text
    assert late_success.json()["job_status"] == "failed", late_success.text

    with SessionLocal() as db:
        expired = db.get(DeviceJob, expired_id)
        assert expired.status == "failed"
        assert expired.last_error == EXPIRED_DELIVERED_ERROR
        assert expired.result == expired_result
        assert expired.completed_at == expired_completed_at
        assert len(_completion_events(db, device_id)) == event_count

    # Idempotency must not bypass Agent authentication.
    unauthorized = client.post(
        f"/api/v1/agents/mikrotik/jobs/{expired_id}/complete",
        headers={**headers, "X-NSM-Device-Secret": "wrong-secret"},
        json={"status": "success", "result": {}},
    )
    assert unauthorized.status_code == 401

    legacy_output = "sent=10 received=10 packet-loss=0% avg-rtt=3ms"
    legacy_complete = client.post(
        f"/api/v1/agents/mikrotik/legacy/jobs/{legacy_id}/complete?status=success",
        headers={**headers, "Content-Type": "text/plain"},
        content=legacy_output,
    )
    assert legacy_complete.status_code == 200, legacy_complete.text
    assert legacy_complete.json() == {"status": "ok"}, legacy_complete.text

    with SessionLocal() as db:
        legacy = db.get(DeviceJob, legacy_id)
        legacy_completed_at = legacy.completed_at
        assert legacy.status == "success"
        assert legacy.result["output"] == legacy_output
        legacy_event_count = len(_completion_events(db, device_id))

    legacy_duplicate = client.post(
        f"/api/v1/agents/mikrotik/legacy/jobs/{legacy_id}/complete?status=failed",
        headers={**headers, "Content-Type": "text/plain"},
        content="late duplicate failure",
    )
    assert legacy_duplicate.status_code == 200, legacy_duplicate.text
    assert legacy_duplicate.json() == {
        "status": "ok",
        "already_terminal": True,
        "job_status": "success",
    }, {"response": legacy_duplicate.json(), "routes": legacy_routes}

    with SessionLocal() as db:
        legacy = db.get(DeviceJob, legacy_id)
        assert legacy.status == "success"
        assert legacy.result["output"] == legacy_output
        assert legacy.completed_at == legacy_completed_at
        assert len(_completion_events(db, device_id)) == legacy_event_count

    print("MikroTik terminal Agent completion idempotency smoke passed")


if __name__ == "__main__":
    main()
