import hashlib
from datetime import timedelta

from sqlalchemy import select

from app.agent_models import DeviceAgentCredential, DeviceJob
from app.backup_maintenance import apply_retention, maintenance_tick
from app.backup_models import BackupArtifact, BackupPolicySettings
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.models import ActionIssue, BackupPolicy, BackupRun, Customer, Device, Notification, utcnow


def seed(now):
    with SessionLocal() as db:
        old = db.scalar(select(Customer).where(Customer.code == "CI09"))
        if old:
            db.delete(old)
            db.commit()
        customer = Customer(name="CI09 Scheduler Lab", code="CI09")
        db.add(customer)
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI09 Router",
            display_name="CI09 Router",
            management_source="mikrotik_agent",
            status="online",
        )
        db.add(device)
        db.flush()
        credential = DeviceAgentCredential(
            device_id=device.id,
            agent_type="mikrotik_agent",
            secret_hash=hashlib.sha256(b"ci09-secret").hexdigest(),
            is_active=True,
            created_at=now - timedelta(hours=1),
            last_used_at=now,
        )
        db.add(credential)
        policy = BackupPolicy(
            name="CI09 Scheduled Backup",
            is_enabled=True,
            scope_type="device",
            device_id=device.id,
            schedule_cron="0 0 * * *",
            binary_backup=True,
            text_export=True,
            pre_firmware_backup=True,
            verify_hash=True,
            retention_daily=1,
            retention_weekly=0,
            retention_monthly=0,
            retry_count=1,
        )
        db.add(policy)
        db.flush()
        local_now = now.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Rome"))
        due = local_now - timedelta(minutes=1)
        db.add(
            BackupPolicySettings(
                policy_id=policy.id,
                schedule_kind="daily",
                schedule_time=f"{due.hour:02d}:{due.minute:02d}",
                options={
                    "mikrotik_binary": True,
                    "mikrotik_export": True,
                    "pre_firmware": True,
                    "verify_hash": True,
                },
            )
        )
        db.commit()
        return customer.id, device.id, credential.id, policy.id


def main():
    now = utcnow().replace(second=0, microsecond=0)
    customer_id, device_id, credential_id, policy_id = seed(now)

    first = maintenance_tick(now)
    assert first["queued"] >= 1, first
    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(DeviceJob).where(
                    DeviceJob.device_id == device_id,
                    DeviceJob.job_type == "backup_mikrotik",
                )
            )
        )
        assert len(jobs) == 1
        job_id = jobs[0].id
        run_id = jobs[0].payload["run_id"]

    maintenance_tick(now + timedelta(seconds=20))
    with SessionLocal() as db:
        # Idempotency is asserted on the test device, not on global counters:
        # previous smoke tests may legitimately have their own due policies.
        own_jobs = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id)))
        assert len(own_jobs) == 1
        job = db.get(DeviceJob, job_id)
        job.status = "delivered"
        job.attempts = 1
        job.delivered_at = now - timedelta(minutes=20)
        db.commit()

    retried = maintenance_tick(now + timedelta(minutes=1))
    assert retried["retried"] >= 1, retried
    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        assert job.status == "pending"
        assert job.not_before is not None
        job.status = "delivered"
        job.attempts = 2
        job.delivered_at = now - timedelta(minutes=20)
        job.not_before = None
        db.commit()

    failed = maintenance_tick(now + timedelta(minutes=2))
    assert failed["failed"] >= 1, failed
    with SessionLocal() as db:
        job = db.get(DeviceJob, job_id)
        run = db.get(BackupRun, run_id)
        assert job.status == "failed"
        assert run.status == "failed"
        issue = db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == device_id,
                ActionIssue.category == "backup",
                ActionIssue.status == "open",
            )
        )
        assert issue is not None
        notification = db.scalar(
            select(Notification).where(
                Notification.device_id == device_id,
                Notification.category == "backup",
                Notification.is_active.is_(True),
            )
        )
        assert notification is not None

        success = BackupRun(
            device_id=device_id,
            policy_id=policy_id,
            started_at=now + timedelta(minutes=3),
            completed_at=now + timedelta(minutes=3),
            status="success",
            backup_type="mikrotik_export",
        )
        db.add(success)
        db.commit()

    recovery = maintenance_tick(now + timedelta(minutes=4))
    assert recovery["recovered"] >= 1, recovery
    with SessionLocal() as db:
        assert db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == device_id,
                ActionIssue.category == "backup",
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        ) is None

    # Retention: latest daily success remains, older CI09 daily artifacts are removed.
    paths = []
    with SessionLocal() as db:
        for days_back in (1, 2):
            stamp = now - timedelta(days=days_back)
            run = BackupRun(
                device_id=device_id,
                policy_id=policy_id,
                started_at=stamp,
                completed_at=stamp,
                status="success",
                backup_type="mikrotik_export",
            )
            db.add(run)
            db.flush()
            path = storage_root() / "ci09" / f"old-{days_back}.rsc"
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = f"# old {days_back}\n".encode()
            path.write_bytes(payload)
            paths.append(path)
            db.add(
                BackupArtifact(
                    run_id=run.id,
                    artifact_type="mikrotik_export",
                    filename=path.name,
                    storage_path=str(path.relative_to(storage_root())),
                    size_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
        db.commit()
        removed = apply_retention(db, now + timedelta(minutes=5))
        db.commit()
        assert removed >= 2, removed
    assert all(not path.exists() for path in paths)

    # Stale agent creates one issue/notification; fresh heartbeat resolves it.
    with SessionLocal() as db:
        credential = db.get(DeviceAgentCredential, credential_id)
        credential.last_used_at = now - timedelta(minutes=30)
        db.commit()
    stale = maintenance_tick(now + timedelta(minutes=6))
    assert stale["agent_changes"] >= 1, stale
    with SessionLocal() as db:
        assert db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == device_id,
                ActionIssue.category == "integration",
                ActionIssue.status == "open",
            )
        ) is not None
        credential = db.get(DeviceAgentCredential, credential_id)
        credential.last_used_at = now + timedelta(minutes=7)
        db.commit()
    healthy = maintenance_tick(now + timedelta(minutes=7))
    assert healthy["agent_changes"] >= 1, healthy
    with SessionLocal() as db:
        assert db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == device_id,
                ActionIssue.category == "integration",
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        ) is None

    print("Core 0.9 scheduler/retry/retention/agent-health smoke test passed")


if __name__ == "__main__":
    main()
