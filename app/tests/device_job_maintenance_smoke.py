from datetime import timedelta

from sqlalchemy import select

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.device_job_maintenance import EXPIRED_PENDING_ERROR, expire_pending_jobs
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

        expired = DeviceJob(
            device_id=device.id,
            job_type="snapshot_section",
            payload={"section": "resources"},
            status="pending",
            expires_at=now - timedelta(seconds=1),
        )
        live = DeviceJob(
            device_id=device.id,
            job_type="diagnostic_ping",
            payload={"target": "192.0.2.1"},
            status="pending",
            expires_at=now + timedelta(minutes=5),
        )
        delivered = DeviceJob(
            device_id=device.id,
            job_type="firmware_readiness",
            payload={},
            status="delivered",
            delivered_at=now - timedelta(minutes=20),
            expires_at=now - timedelta(minutes=10),
        )
        backup = DeviceJob(
            device_id=device.id,
            job_type="backup_mikrotik",
            payload={},
            status="pending",
            expires_at=now - timedelta(minutes=1),
        )
        db.add_all([expired, live, delivered, backup])
        db.commit()
        return expired.id, live.id, delivered.id, backup.id


def main():
    now = utcnow().replace(microsecond=0)
    expired_id, live_id, delivered_id, backup_id = seed(now)

    assert expire_pending_jobs(now) == 1

    with SessionLocal() as db:
        expired = db.get(DeviceJob, expired_id)
        live = db.get(DeviceJob, live_id)
        delivered = db.get(DeviceJob, delivered_id)
        backup = db.get(DeviceJob, backup_id)

        assert expired.status == "failed"
        assert expired.completed_at is not None
        assert expired.last_error == EXPIRED_PENDING_ERROR

        assert live.status == "pending"
        assert live.completed_at is None

        assert delivered.status == "delivered"
        assert delivered.completed_at is None

        # Backup expiry/retry remains owned by backup_maintenance.
        assert backup.status == "pending"
        assert backup.completed_at is None

    assert expire_pending_jobs(now) == 0
    print("Expired pending Agent job maintenance smoke test passed")


if __name__ == "__main__":
    main()
