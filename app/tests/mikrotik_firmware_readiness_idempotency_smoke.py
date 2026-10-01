from datetime import timedelta
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent as agent_core
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, utcnow

SECRET = "ci-firmware-readiness-terminal-secret"


def _completion_events(db, device_id):
    return list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.device_id == device_id,
                AuditEvent.event_type == "FIRMWARE_READINESS_COMPLETED",
            )
        )
    )


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow().replace(microsecond=0)
    with SessionLocal() as db:
        customer = Customer(
            name=f"CI firmware terminal {suffix}",
            code=f"FT{suffix[:6]}",
        )
        db.add(customer)
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI firmware terminal router",
            display_name="CI firmware terminal router",
            management_source="mikrotik_agent",
            status="online",
            firmware_version="7.20.7",
            recommended_firmware_version="7.21.0",
            firmware_status="update_available",
            routerboot_version="7.20.7",
            inventory_data={
                "firmware_readiness": {
                    "channel": "stable",
                    "installed_version": "7.20.7",
                    "latest_version": "7.21.0",
                    "status": "New version is available",
                    "source": "synthetic_ci",
                }
            },
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
        job = DeviceJob(
            device_id=device.id,
            job_type="firmware_readiness",
            status="failed",
            payload={"read_only": True},
            result={"maintenance": True},
            delivered_at=now - timedelta(minutes=12),
            completed_at=now - timedelta(minutes=1),
            expires_at=now - timedelta(minutes=2),
            last_error="synthetic stale delivered timeout",
        )
        db.add(job)
        db.commit()
        return device.id, job.id


def main():
    device_id, job_id = seed()
    headers = {
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": SECRET,
    }
    client = TestClient(app)

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        job = db.get(DeviceJob, job_id)
        original_inventory = dict(device.inventory_data or {})
        original_completed_at = job.completed_at
        original_result = dict(job.result or {})
        original_events = len(_completion_events(db, device_id))

    # A delayed success after maintenance already failed the job must be an
    # authenticated no-op. In particular it must not rewrite observed firmware
    # state with stale readiness data.
    late_success = client.post(
        f"/api/v1/agents/mikrotik/firmware-readiness/{job_id}/complete",
        headers=headers,
        json={
            "status": "success",
            "error": "",
            "result": {
                "channel": "testing",
                "installed_version": "9.99.9",
                "latest_version": "10.0.0",
                "status": "synthetic late result",
                "free_hdd_space": "1GiB",
                "routerboard_current": "9.99.9",
                "routerboard_upgrade": "10.0.0",
            },
        },
    )
    assert late_success.status_code == 200, late_success.text
    assert late_success.json() == {
        "status": "ok",
        "already_terminal": True,
        "job_status": "failed",
    }, late_success.text

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        job = db.get(DeviceJob, job_id)
        assert job.status == "failed"
        assert job.result == original_result
        assert job.last_error == "synthetic stale delivered timeout"
        assert job.completed_at == original_completed_at
        assert device.firmware_version == "7.20.7"
        assert device.recommended_firmware_version == "7.21.0"
        assert device.firmware_status == "update_available"
        assert device.routerboot_version == "7.20.7"
        assert device.inventory_data == original_inventory
        assert len(_completion_events(db, device_id)) == original_events

    invalid_status = client.post(
        f"/api/v1/agents/mikrotik/firmware-readiness/{job_id}/complete",
        headers=headers,
        json={"status": "maybe", "result": {}},
    )
    assert invalid_status.status_code == 400, invalid_status.text

    unauthorized = client.post(
        f"/api/v1/agents/mikrotik/firmware-readiness/{job_id}/complete",
        headers={**headers, "X-NSM-Device-Secret": "wrong-secret"},
        json={"status": "success", "result": {}},
    )
    assert unauthorized.status_code == 401, unauthorized.text

    print("MikroTik terminal firmware readiness idempotency smoke passed")


if __name__ == "__main__":
    main()
