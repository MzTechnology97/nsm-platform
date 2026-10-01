from datetime import timedelta
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent as agent_core
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.firmware_upgrade_models import FirmwareUpgradePlan
from app.models import AuditEvent, Customer, Device, utcnow

SECRET = "ci-firmware-stage-terminal-secret"


def _stage_events(db, device_id):
    return list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.device_id == device_id,
                AuditEvent.event_type == "FIRMWARE_PACKAGE_STAGING_COMPLETED",
            )
        )
    )


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow().replace(microsecond=0)
    with SessionLocal() as db:
        customer = Customer(name=f"CI stage terminal {suffix}", code=f"ST{suffix[:6]}")
        db.add(customer)
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI stage terminal router",
            display_name="CI stage terminal router",
            management_source="mikrotik_agent",
            status="online",
            firmware_version="7.20.0",
            recommended_firmware_version="7.21.1",
            inventory_data={"agent_transport": "modern"},
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
        plan = FirmwareUpgradePlan(
            device_id=device.id,
            target_version="7.21.1",
            channel="stable",
            status="staged",
            started_at=now - timedelta(minutes=3),
            precheck_data={
                "staging": {
                    "completed_at": (now - timedelta(minutes=1)).isoformat(),
                    "target_version": "7.21.1",
                    "download_only": True,
                    "result": {"latest_version": "7.21.1"},
                }
            },
        )
        db.add(plan)
        db.flush()
        job = DeviceJob(
            device_id=device.id,
            job_type="firmware_stage",
            status="success",
            payload={
                "plan_id": str(plan.id),
                "target_version": "7.21.1",
                "channel": "stable",
                "download_only": True,
            },
            result={"target_version": "7.21.1", "download_only": True},
            delivered_at=now - timedelta(minutes=2),
            completed_at=now - timedelta(minutes=1),
        )
        db.add(job)
        db.commit()
        return device.id, plan.id, job.id


def main():
    device_id, plan_id, job_id = seed()
    headers = {
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": SECRET,
    }
    client = TestClient(app)

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        job = db.get(DeviceJob, job_id)
        original_plan_data = dict(plan.precheck_data or {})
        original_result = dict(job.result or {})
        original_completed_at = job.completed_at
        original_events = len(_stage_events(db, device_id))

    # A duplicated contradictory report after a successful stage must not turn
    # the job or its approved firmware plan into failed state.
    duplicate_failure = client.post(
        f"/api/v1/agents/mikrotik/firmware-stage/{job_id}/complete",
        headers=headers,
        json={
            "status": "failed",
            "error": "synthetic late duplicate failure",
            "result": {"target_version": "7.21.1", "download_only": True},
        },
    )
    assert duplicate_failure.status_code == 200, duplicate_failure.text
    assert duplicate_failure.json() == {
        "status": "ok",
        "already_terminal": True,
        "job_status": "success",
    }, duplicate_failure.text

    with SessionLocal() as db:
        plan = db.get(FirmwareUpgradePlan, plan_id)
        job = db.get(DeviceJob, job_id)
        assert job.status == "success"
        assert job.result == original_result
        assert job.last_error is None
        assert job.completed_at == original_completed_at
        assert plan.status == "staged"
        assert plan.last_error is None
        assert plan.precheck_data == original_plan_data
        assert len(_stage_events(db, device_id)) == original_events

    invalid_status = client.post(
        f"/api/v1/agents/mikrotik/firmware-stage/{job_id}/complete",
        headers=headers,
        json={"status": "maybe", "result": {}},
    )
    assert invalid_status.status_code == 400, invalid_status.text

    unauthorized = client.post(
        f"/api/v1/agents/mikrotik/firmware-stage/{job_id}/complete",
        headers={**headers, "X-NSM-Device-Secret": "wrong-secret"},
        json={"status": "success", "result": {}},
    )
    assert unauthorized.status_code == 401, unauthorized.text

    print("MikroTik terminal firmware staging idempotency smoke passed")


if __name__ == "__main__":
    main()
