from datetime import timedelta

from sqlalchemy import select

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.device_job_maintenance import (
    EXPIRED_DELIVERED_ERROR,
    EXPIRED_PENDING_ERROR,
    expire_delivered_jobs,
    expire_pending_jobs,
)
from app.models import Customer, Device, utcnow


def seed(now):
    with SessionLocal() as db:
        existing = db.scalar(select(Customer).where(Customer.code == "CI-JOB-EXP"))
        if existing:
            db.delete(existing)
            db.commit()

        customer = Customer(name="CI Job Expiry Lab", code="CI-JOB-EXP")
        db.add(customer)
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI Job Expiry Router",
            display_name="CI Job Expiry Router",
            management_source="mikrotik_agent",
            status="online",
        )
        db.add(device)
        db.flush()

        expired_pending = DeviceJob(
            device_id=device.id,
            job_type="snapshot_section",
            payload={"section": "resources"},
            status="pending",
            expires_at=now - timedelta(seconds=1),
        )
        live_pending = DeviceJob(
            device_id=device.id,
            job_type="diagnostic_ping",
            payload={"target": "192.0.2.1"},
            status="pending",
            expires_at=now + timedelta(minutes=5),
        )
        expired_delivered = DeviceJob(
            device_id=device.id,
            job_type="firmware_readiness",
            payload={},
            status="delivered",
            delivered_at=now - timedelta(minutes=20),
            expires_at=now - timedelta(minutes=10),
        )
        live_delivered = DeviceJob(
            device_id=device.id,
            job_type="diagnostic_logs",
            payload={},
            status="delivered",
            delivered_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=4),
        )
        running = DeviceJob(
            device_id=device.id,
            job_type="diagnostic_traceroute",
            payload={"target": "198.51.100.1"},
            status="running",
            delivered_at=now - timedelta(minutes=20),
            expires_at=now - timedelta(minutes=10),
        )
        backup_pending = DeviceJob(
            device_id=device.id,
            job_type="backup_mikrotik",
            payload={},
            status="pending",
            expires_at=now - timedelta(minutes=1),
        )
        backup_delivered = DeviceJob(
            device_id=device.id,
            job_type="backup_mikrotik",
            payload={},
            status="delivered",
            delivered_at=now - timedelta(minutes=20),
            expires_at=now - timedelta(minutes=10),
        )
        db.add_all(
            [
                expired_pending,
                live_pending,
                expired_delivered,
                live_delivered,
                running,
                backup_pending,
                backup_delivered,
            ]
        )
        db.commit()
        return (
            expired_pending.id,
            live_pending.id,
            expired_delivered.id,
            live_delivered.id,
            running.id,
            backup_pending.id,
            backup_delivered.id,
        )


def main():
    now = utcnow().replace(microsecond=0)
    (
        expired_pending_id,
        live_pending_id,
        expired_delivered_id,
        live_delivered_id,
        running_id,
        backup_pending_id,
        backup_delivered_id,
    ) = seed(now)

    assert expire_pending_jobs(now) == 1
    assert expire_delivered_jobs(now) == 1

    with SessionLocal() as db:
        expired_pending = db.get(DeviceJob, expired_pending_id)
        live_pending = db.get(DeviceJob, live_pending_id)
        expired_delivered = db.get(DeviceJob, expired_delivered_id)
        live_delivered = db.get(DeviceJob, live_delivered_id)
        running = db.get(DeviceJob, running_id)
        backup_pending = db.get(DeviceJob, backup_pending_id)
        backup_delivered = db.get(DeviceJob, backup_delivered_id)

        assert expired_pending.status == "failed"
        assert expired_pending.completed_at is not None
        assert expired_pending.last_error == EXPIRED_PENDING_ERROR

        assert live_pending.status == "pending"
        assert live_pending.completed_at is None

        assert expired_delivered.status == "failed"
        assert expired_delivered.completed_at is not None
        assert expired_delivered.last_error == EXPIRED_DELIVERED_ERROR

        assert live_delivered.status == "delivered"
        assert live_delivered.completed_at is None

        # Running jobs keep their domain-specific completion/timeout lifecycle.
        assert running.status == "running"
        assert running.completed_at is None

        # Backup expiry/retry remains owned by backup_maintenance.
        assert backup_pending.status == "pending"
        assert backup_pending.completed_at is None
        assert backup_delivered.status == "delivered"
        assert backup_delivered.completed_at is None

    assert expire_pending_jobs(now) == 0
    assert expire_delivered_jobs(now) == 0
    print("Expired pending/delivered Agent job maintenance smoke test passed")


if __name__ == "__main__":
    main()
