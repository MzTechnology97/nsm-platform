import hashlib
from datetime import timedelta

from sqlalchemy import select

from app.agent_health_automation import (
    TITLE_LEGACY_UNREACHABLE,
    TITLE_NO_CREDENTIAL,
    TITLE_PENDING_ENROLLMENT,
    TITLE_UNKNOWN_TRANSPORT,
    agent_health_tick,
)
from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.models import ActionIssue, Customer, Device, DeviceEnrollment, Notification, utcnow


def main():
    now = utcnow().replace(microsecond=0)
    with SessionLocal() as db:
        old = db.scalar(select(Customer).where(Customer.code == "CI36"))
        if old:
            db.delete(old)
            db.commit()

        customer = Customer(name="CI36 Agent Health Automation", code="CI36")
        db.add(customer)
        db.flush()
        customer_id = customer.id

        missing = Device(
            customer_id=customer_id,
            vendor="mikrotik",
            device_type="router",
            name="CI36 missing credential",
            display_name="CI36 missing credential",
            status="unknown",
        )
        pending = Device(
            customer_id=customer_id,
            vendor="mikrotik",
            device_type="router",
            name="CI36 pending enrollment",
            display_name="CI36 pending enrollment",
            status="unknown",
        )
        unknown = Device(
            customer_id=customer_id,
            vendor="mikrotik",
            device_type="router",
            name="CI36 unknown transport",
            display_name="CI36 unknown transport",
            status="online",
            inventory_data={},
        )
        db.add_all([missing, pending, unknown])
        db.flush()

        db.add(
            DeviceEnrollment(
                device_id=pending.id,
                source="mikrotik",
                token_hash=hashlib.sha256(b"ci36-pending-token").hexdigest(),
                status="pending",
                created_at=now - timedelta(minutes=11),
                expires_at=now + timedelta(minutes=19),
            )
        )
        db.add(
            DeviceAgentCredential(
                device_id=unknown.id,
                agent_type="mikrotik_agent",
                secret_hash=hashlib.sha256(b"ci36-unknown").hexdigest(),
                is_active=True,
                created_at=now - timedelta(hours=1),
                last_used_at=now,
            )
        )
        db.commit()
        missing_id = missing.id
        pending_id = pending.id
        unknown_id = unknown.id

    first = agent_health_tick(now)
    assert first["agent_attention_changes"] >= 3, first

    with SessionLocal() as db:
        missing_issue = db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == missing_id,
                ActionIssue.title == TITLE_NO_CREDENTIAL,
                ActionIssue.status == "open",
            )
        )
        assert missing_issue is not None
        assert missing_issue.severity == "high"

        pending_issue = db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == pending_id,
                ActionIssue.title == TITLE_PENDING_ENROLLMENT,
                ActionIssue.status == "open",
            )
        )
        assert pending_issue is not None
        assert db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == pending_id,
                ActionIssue.title == TITLE_NO_CREDENTIAL,
                ActionIssue.status == "open",
            )
        ) is None

        unknown_issue = db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == unknown_id,
                ActionIssue.title == TITLE_UNKNOWN_TRANSPORT,
                ActionIssue.status == "open",
            )
        )
        assert unknown_issue is not None

        missing = db.get(Device, missing_id)
        missing.status = "online"
        missing.inventory_data = {
            "agent_version": "0.36.0",
            "agent_transport": "modern",
            "enrollment_transport": "bodyless-v1",
        }
        db.add(
            DeviceAgentCredential(
                device_id=missing.id,
                agent_type="mikrotik_agent",
                secret_hash=hashlib.sha256(b"ci36-recovered").hexdigest(),
                is_active=True,
                created_at=now,
                last_used_at=now + timedelta(minutes=1),
            )
        )
        db.commit()

    recovered = agent_health_tick(now + timedelta(minutes=1))
    assert recovered["agent_attention_changes"] >= 1, recovered

    with SessionLocal() as db:
        assert db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == missing_id,
                ActionIssue.title == TITLE_NO_CREDENTIAL,
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        ) is None
        failure_notification = db.scalar(
            select(Notification)
            .where(
                Notification.device_id == missing_id,
                Notification.title == TITLE_NO_CREDENTIAL,
            )
            .order_by(Notification.created_at.desc())
        )
        assert failure_notification is not None
        assert failure_notification.is_active is False
        recoveries = list(
            db.scalars(
                select(Notification).where(
                    Notification.device_id == missing_id,
                    Notification.title == "Recovery agent MikroTik",
                )
            )
        )
        assert len(recoveries) == 1
        assert recoveries[0].severity == "info"
        assert recoveries[0].source_url == f"/devices/{missing_id}/agent"

        legacy_issue = ActionIssue(
            category="integration",
            severity="high",
            status="resolved",
            title=TITLE_LEGACY_UNREACHABLE,
            details={"message": "historical stale heartbeat"},
            customer_id=customer_id,
            device_id=missing_id,
            created_at=now - timedelta(minutes=20),
            updated_at=now,
            resolved_at=now,
        )
        db.add(legacy_issue)
        db.commit()
        legacy_issue_id = legacy_issue.id

    legacy_recovery = agent_health_tick(now + timedelta(minutes=2))
    assert legacy_recovery["agent_attention_changes"] >= 1, legacy_recovery
    with SessionLocal() as db:
        issue = db.get(ActionIssue, legacy_issue_id)
        assert (issue.details or {}).get("recovery_notified_at")
        recovery_count = len(
            list(
                db.scalars(
                    select(Notification).where(
                        Notification.device_id == missing_id,
                        Notification.title == "Recovery agent MikroTik",
                    )
                )
            )
        )

    agent_health_tick(now + timedelta(minutes=3))
    with SessionLocal() as db:
        recovery_count_after = len(
            list(
                db.scalars(
                    select(Notification).where(
                        Notification.device_id == missing_id,
                        Notification.title == "Recovery agent MikroTik",
                    )
                )
            )
        )
        assert recovery_count_after == recovery_count

    print("Core 0.36 agent health automation and recovery smoke passed")


if __name__ == "__main__":
    main()
