from datetime import timedelta
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent as agent_core
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, BackupRun, Customer, Device, utcnow

SECRET = "ci-backup-final-outcome-secret"


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow().replace(microsecond=0)
    with SessionLocal() as db:
        customer = Customer(
            name=f"CI backup outcome {suffix}",
            code=f"BO{suffix[:6]}",
        )
        db.add(customer)
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI backup outcome router",
            display_name="CI backup outcome router",
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

        run = BackupRun(
            device_id=device.id,
            status="in_progress",
            backup_type="mikrotik_multi",
            started_at=now - timedelta(minutes=1),
        )
        db.add(run)
        db.flush()
        job = DeviceJob(
            device_id=device.id,
            job_type="backup_mikrotik",
            status="running",
            payload={
                "run_id": str(run.id),
                "formats": ["mikrotik_binary", "mikrotik_export"],
            },
            delivered_at=now - timedelta(minutes=1),
        )
        db.add(job)
        db.commit()
        return device.id, job.id, run.id


def main():
    device_id, job_id, run_id = seed()
    headers = {
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": SECRET,
    }
    client = TestClient(app)

    # The Agent reports success, but no required artifacts were completed. The
    # finalizer is authoritative and must downgrade both BackupRun and DeviceJob.
    complete = client.post(
        f"/api/v1/agents/mikrotik/backup-jobs/{job_id}/complete",
        headers=headers,
        json={"status": "success", "result": {"agent_version": "ci-outcome"}},
    )
    assert complete.status_code == 200, complete.text
    assert complete.json() == {"status": "ok"}, complete.text

    expected_error = "Artefatti mancanti: mikrotik_binary, mikrotik_export"
    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        run = db.get(BackupRun, run_id)
        assert job.status == "failed", {
            "job_status": job.status,
            "run_status": run.status,
            "job_error": job.last_error,
            "run_error": run.error_message,
        }
        assert run.status == "failed"
        assert job.last_error == expected_error
        assert run.error_message == expected_error
        assert job.completed_at is not None
        assert run.completed_at is not None

        events = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.device_id == device_id,
                    AuditEvent.event_type.in_(["BACKUP_COMPLETED", "BACKUP_FAILED"]),
                )
            )
        )
        assert len(events) == 1
        assert events[0].event_type == "BACKUP_FAILED"
        assert events[0].result == "failed"

    print("MikroTik backup final outcome consistency smoke passed")


if __name__ == "__main__":
    main()
