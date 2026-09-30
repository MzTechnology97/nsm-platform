import uuid

from sqlalchemy import select

from app import backup_maintenance
from app import mikrotik_backup as backup
from app import mikrotik_backup_agent as backup_agent
from app import mikrotik_legacy_jobs as legacy_jobs
from app.agent_models import DeviceJob
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.entrypoint import app  # noqa: F401 - installs the finalization wrapper
from app.mikrotik_backup_models import BackupUploadSession, MikrotikBackupJobSecret
from app.models import BackupPolicy, BackupRun, Customer, Device


CUSTOMER_CODE = "TEST-BACKUP-CLEANUP"


def seed():
    with SessionLocal() as db:
        old = db.scalar(select(Customer).where(Customer.code == CUSTOMER_CODE))
        if old:
            db.delete(old)
            db.commit()

        customer = Customer(name="Test Backup Cleanup", code=CUSTOMER_CODE)
        db.add(customer)
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="TEST-BACKUP-CLEANUP-R1",
            display_name="Test Backup Cleanup Router",
            management_source="mikrotik_agent",
            status="online",
        )
        db.add(device)
        db.flush()
        policy = BackupPolicy(
            name="Test Backup Cleanup Policy",
            is_enabled=True,
            scope_type="device",
            device_id=device.id,
            schedule_cron="0 3 * * *",
            binary_backup=True,
            text_export=True,
            pre_firmware_backup=True,
            verify_hash=True,
        )
        db.add(policy)
        db.flush()
        run = BackupRun(
            device_id=device.id,
            policy_id=policy.id,
            status="in_progress",
            backup_type="mikrotik_multi",
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
                "cleanup_router_files": True,
            },
        )
        db.add(job)
        db.flush()
        db.add(
            MikrotikBackupJobSecret(
                job_id=job.id,
                encrypted_backup_password="synthetic-encrypted-password",
            )
        )

        incoming = storage_root() / ".incoming" / str(job.id)
        incoming.mkdir(parents=True, exist_ok=True)
        partial_path = incoming / "mikrotik_binary.part"
        partial_path.write_bytes(b"synthetic-partial-data")
        partial_rel = str(partial_path.resolve().relative_to(storage_root()))

        partial = BackupUploadSession(
            job_id=job.id,
            device_id=device.id,
            artifact_type="mikrotik_binary",
            filename="synthetic.backup",
            temp_path=partial_rel,
            expected_size=100,
            received_size=len(b"synthetic-partial-data"),
            status="receiving",
        )
        completed = BackupUploadSession(
            job_id=job.id,
            device_id=device.id,
            artifact_type="mikrotik_export",
            filename="synthetic.rsc",
            temp_path=str((incoming / "mikrotik_export.part").resolve().relative_to(storage_root())),
            expected_size=20,
            received_size=20,
            status="complete",
            sha256="a" * 64,
        )
        db.add_all([partial, completed])
        db.commit()
        return customer.id, device.id, run.id, job.id, partial.id, completed.id, partial_path


def main():
    # Direct by-value imports share the canonical finalizer. The modern Agent
    # path deliberately remains a composed wrapper because config drift is
    # installed first; cleanup must wrap that callable rather than replace it.
    assert legacy_jobs.finalize_backup_job is backup.finalize_backup_job
    assert backup_maintenance.finalize_backup_job is backup.finalize_backup_job
    assert getattr(backup_agent, "_config_drift_hooked", False)
    assert getattr(backup_agent.finalize_backup_job, "_nsm_backup_terminal_cleanup", False)

    customer_id, device_id, run_id, job_id, partial_id, completed_id, partial_path = seed()
    assert partial_path.is_file()

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        job = db.get(DeviceJob, job_id)
        # Exercise the actual modern Agent completion callable, including the
        # pre-existing configuration-drift composition.
        backup_agent.finalize_backup_job(
            db, device, job, False, "synthetic upload failure"
        )
        db.commit()

    with SessionLocal() as db:
        run = db.get(BackupRun, run_id)
        partial = db.get(BackupUploadSession, partial_id)
        completed = db.get(BackupUploadSession, completed_id)
        secret = db.get(MikrotikBackupJobSecret, job_id)

        assert run.status == "failed"
        assert run.error_message == "synthetic upload failure"
        assert partial.status == "failed"
        assert partial.completed_at is not None
        assert completed.status == "complete"
        assert secret is None
        assert not partial_path.exists()

        customer = db.get(Customer, customer_id)
        db.delete(customer)
        db.commit()

    print("MikroTik backup finalization cleanup smoke test passed")


if __name__ == "__main__":
    main()
