from datetime import timedelta
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent as agent_core
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_backup_models import BackupUploadSession
from app.models import AuditEvent, BackupRun, Customer, Device, utcnow

SECRET = "ci-backup-terminal-secret"


def _backup_completion_events(db, device_id):
    return list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.device_id == device_id,
                AuditEvent.event_type.in_(["BACKUP_COMPLETED", "BACKUP_FAILED"]),
            )
        )
    )


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow().replace(microsecond=0)
    with SessionLocal() as db:
        customer = Customer(
            name=f"CI backup terminal {suffix}",
            code=f"BT{suffix[:6]}",
        )
        db.add(customer)
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI backup terminal router",
            display_name="CI backup terminal router",
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

        success_run = BackupRun(
            device_id=device.id,
            status="success",
            backup_type="mikrotik_multi",
            started_at=now - timedelta(minutes=3),
            completed_at=now - timedelta(minutes=2),
            size_bytes=30,
        )
        failed_run = BackupRun(
            device_id=device.id,
            status="failed",
            backup_type="mikrotik_multi",
            started_at=now - timedelta(minutes=4),
            completed_at=now - timedelta(minutes=1),
            error_message="synthetic maintenance failure",
        )
        db.add_all([success_run, failed_run])
        db.flush()

        success_job = DeviceJob(
            device_id=device.id,
            job_type="backup_mikrotik",
            status="success",
            payload={
                "run_id": str(success_run.id),
                "formats": ["mikrotik_binary", "mikrotik_export"],
            },
            result={"agent_version": "ci-terminal"},
            delivered_at=now - timedelta(minutes=3),
            completed_at=now - timedelta(minutes=2),
        )
        failed_job = DeviceJob(
            device_id=device.id,
            job_type="backup_mikrotik",
            status="failed",
            payload={
                "run_id": str(failed_run.id),
                "formats": ["mikrotik_binary", "mikrotik_export"],
            },
            result={"maintenance": True},
            delivered_at=now - timedelta(minutes=4),
            completed_at=now - timedelta(minutes=1),
            last_error="synthetic maintenance failure",
        )
        db.add_all([success_job, failed_job])
        db.flush()

        for artifact_type, suffix_name in (
            ("mikrotik_binary", "backup"),
            ("mikrotik_export", "rsc"),
        ):
            db.add(
                BackupUploadSession(
                    job_id=success_job.id,
                    device_id=device.id,
                    artifact_type=artifact_type,
                    filename=f"synthetic.{suffix_name}",
                    temp_path=f"synthetic/{success_job.id}/{suffix_name}",
                    expected_size=15,
                    received_size=15,
                    status="complete",
                    sha256=("a" if artifact_type == "mikrotik_binary" else "b") * 64,
                    completed_at=now - timedelta(minutes=2),
                )
            )

        db.commit()
        return device.id, success_job.id, failed_job.id, success_run.id, failed_run.id


def main():
    device_id, success_job_id, failed_job_id, success_run_id, failed_run_id = seed()
    headers = {
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": SECRET,
    }
    client = TestClient(app)

    with SessionLocal() as db:
        success_job = db.get(DeviceJob, success_job_id)
        failed_job = db.get(DeviceJob, failed_job_id)
        success_run = db.get(BackupRun, success_run_id)
        failed_run = db.get(BackupRun, failed_run_id)
        success_completed_at = success_job.completed_at
        failed_completed_at = failed_job.completed_at
        success_run_completed_at = success_run.completed_at
        failed_run_completed_at = failed_run.completed_at
        success_result = dict(success_job.result)
        failed_result = dict(failed_job.result)
        event_count = len(_backup_completion_events(db, device_id))

    duplicate_failure = client.post(
        f"/api/v1/agents/mikrotik/backup-jobs/{success_job_id}/complete",
        headers=headers,
        json={
            "status": "failed",
            "error": "late duplicate must not replace success",
            "result": {"agent_version": "late"},
        },
    )
    assert duplicate_failure.status_code == 200, duplicate_failure.text
    assert duplicate_failure.json() == {
        "status": "ok",
        "already_terminal": True,
        "job_status": "success",
    }, duplicate_failure.text

    with SessionLocal() as db:
        job = db.get(DeviceJob, success_job_id)
        run = db.get(BackupRun, success_run_id)
        assert job.status == "success"
        assert job.result == success_result
        assert job.last_error is None
        assert job.completed_at == success_completed_at
        assert run.status == "success"
        assert run.completed_at == success_run_completed_at
        assert run.error_message is None
        assert len(_backup_completion_events(db, device_id)) == event_count

    late_success = client.post(
        f"/api/v1/agents/mikrotik/backup-jobs/{failed_job_id}/complete",
        headers=headers,
        json={"status": "success", "result": {"agent_version": "late"}},
    )
    assert late_success.status_code == 200, late_success.text
    assert late_success.json() == {
        "status": "ok",
        "already_terminal": True,
        "job_status": "failed",
    }, late_success.text

    with SessionLocal() as db:
        job = db.get(DeviceJob, failed_job_id)
        run = db.get(BackupRun, failed_run_id)
        assert job.status == "failed"
        assert job.result == failed_result
        assert job.last_error == "synthetic maintenance failure"
        assert job.completed_at == failed_completed_at
        assert run.status == "failed"
        assert run.completed_at == failed_run_completed_at
        assert run.error_message == "synthetic maintenance failure"
        assert len(_backup_completion_events(db, device_id)) == event_count

    invalid_status = client.post(
        f"/api/v1/agents/mikrotik/backup-jobs/{success_job_id}/complete",
        headers=headers,
        json={"status": "maybe", "result": {}},
    )
    assert invalid_status.status_code == 400, invalid_status.text

    unauthorized = client.post(
        f"/api/v1/agents/mikrotik/backup-jobs/{success_job_id}/complete",
        headers={**headers, "X-NSM-Device-Secret": "wrong-secret"},
        json={"status": "success", "result": {}},
    )
    assert unauthorized.status_code == 401, unauthorized.text

    print("MikroTik backup terminal completion idempotency smoke passed")


if __name__ == "__main__":
    main()
