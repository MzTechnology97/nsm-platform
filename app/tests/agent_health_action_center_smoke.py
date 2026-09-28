import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, Customer, Device, DeviceEnrollment, Notification, User, utcnow
from app.platform_maintenance import maintenance_tick
from app.security import hash_password

PASSWORD = "CI36-Agent-Health-2026"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "csrf token missing"
    return match.group(1)


def seed(now):
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"ci36-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="CI36 Agent Health",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"CI36 Customer {suffix}", code=f"H36{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()

        def device(label, *, status="online", source="mikrotik_agent", last_seen=None, inventory=None):
            item = Device(
                customer_id=customer.id,
                vendor="mikrotik",
                device_type="router",
                name=f"CI36 {label}",
                display_name=f"CI36 {label}",
                device_identity=f"CI36-{label.upper().replace(' ', '-')}",
                management_source=source,
                status=status,
                last_seen=last_seen,
                inventory_data=inventory or {},
            )
            db.add(item)
            db.flush()
            return item

        stale = device(
            "Stale",
            last_seen=now - timedelta(minutes=31),
            inventory={"agent_version": "0.20.0", "agent_transport": "modern"},
        )
        offline = device(
            "Offline",
            status="offline",
            last_seen=now,
            inventory={"agent_version": "0.20.0", "agent_transport": "modern"},
        )
        no_credential = device(
            "No Credential",
            status="offline",
            inventory={"agent_transport": "modern"},
        )
        pending = device("Pending", status="pending_enrollment")
        healthy = device(
            "Healthy",
            last_seen=now,
            inventory={"agent_version": "0.20.0-legacy", "agent_transport": "legacy"},
        )
        manual = device("Manual", status="offline", source="manual")

        credentials = {}
        for marker, item, stamp in (
            ("a", stale, now - timedelta(minutes=31)),
            ("b", offline, now),
            ("c", healthy, now),
        ):
            credential = DeviceAgentCredential(
                device_id=item.id,
                agent_type="mikrotik_agent",
                secret_hash=marker * 64,
                is_active=True,
                created_at=now - timedelta(hours=1),
                last_used_at=stamp,
            )
            db.add(credential)
            db.flush()
            credentials[item.id] = credential.id

        db.add(
            DeviceEnrollment(
                device_id=pending.id,
                source="mikrotik_bootstrap",
                token_hash="d" * 64,
                status="pending",
                expires_at=now + timedelta(minutes=30),
                created_by_user_id=user.id,
            )
        )

        # Simulate the pre-0.36 generic alert. The first new maintenance tick
        # must resolve it and replace it with the typed stale condition.
        old_issue = ActionIssue(
            category="integration",
            severity="high",
            status="open",
            title="Agent MikroTik non raggiungibile",
            details={"message": "legacy generic issue"},
            customer_id=customer.id,
            device_id=stale.id,
        )
        db.add(old_issue)
        db.add(
            Notification(
                severity="high",
                category="integration",
                title="Agent MikroTik non raggiungibile",
                message="legacy generic issue",
                customer_id=customer.id,
                device_id=stale.id,
                source_url=f"/devices/{stale.id}",
                is_active=True,
            )
        )
        db.commit()
        return {
            "username": user.username,
            "customer_id": customer.id,
            "stale": stale.id,
            "offline": offline.id,
            "no_credential": no_credential.id,
            "pending": pending.id,
            "healthy": healthy.id,
            "manual": manual.id,
            "credentials": credentials,
        }


def login(client, username):
    page = client.get("/login")
    token = _csrf(page.text)
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def main():
    now = utcnow().replace(microsecond=0)
    ids = seed(now)

    first = maintenance_tick(now)
    assert first["agent_changes"] >= 4, first

    with SessionLocal() as db:
        open_issues = list(
            db.scalars(
                select(ActionIssue).where(
                    ActionIssue.category == "agent_health",
                    ActionIssue.status.in_(["open", "acknowledged"]),
                )
            )
        )
        assert len(open_issues) == 3, [(item.title, item.device_id) for item in open_issues]
        by_device = {item.device_id: item for item in open_issues}
        assert by_device[ids["stale"]].title == "Heartbeat agent MikroTik stale"
        assert by_device[ids["stale"]].severity == "warning"
        assert by_device[ids["offline"]].title == "Agent MikroTik offline"
        assert by_device[ids["no_credential"]].title == "Credenziale agent MikroTik assente"
        assert ids["pending"] not in by_device
        assert ids["healthy"] not in by_device
        assert ids["manual"] not in by_device

        old = db.scalar(
            select(ActionIssue).where(
                ActionIssue.device_id == ids["stale"],
                ActionIssue.category == "integration",
                ActionIssue.title == "Agent MikroTik non raggiungibile",
            )
        )
        assert old.status == "resolved"
        old_notification = db.scalar(
            select(Notification).where(
                Notification.device_id == ids["stale"],
                Notification.category == "integration",
            )
        )
        assert old_notification.is_active is False

        notifications = list(
            db.scalars(
                select(Notification).where(
                    Notification.category == "agent_health",
                    Notification.is_active.is_(True),
                )
            )
        )
        assert len(notifications) == 3
        opened_events = db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.event_type == "AGENT_HEALTH_ISSUE_OPENED")
        )
        assert opened_events == 3

    # Same unhealthy conditions must update existing issues, not duplicate them.
    second = maintenance_tick(now + timedelta(minutes=1))
    assert second["agent_changes"] == 0, second
    with SessionLocal() as db:
        assert db.scalar(
            select(func.count(ActionIssue.id)).where(ActionIssue.category == "agent_health")
        ) == 3

    client = TestClient(app)
    login(client, ids["username"])
    page = client.get(f"/action-center?category=agent_health&customer={ids['customer_id']}")
    assert page.status_code == 200, page.text
    assert "agent health" in page.text
    assert "Heartbeat agent MikroTik stale" in page.text
    assert "Agent MikroTik offline" in page.text
    assert "Credenziale agent MikroTik assente" in page.text
    assert "CI36 Pending" not in page.text
    assert "CI36 Manual" not in page.text

    # Restore all three incident devices to a healthy authenticated state.
    recovery_time = now + timedelta(minutes=2)
    with SessionLocal() as db:
        stale = db.get(Device, ids["stale"])
        stale.status = "online"
        stale.last_seen = recovery_time
        stale_credential = db.get(DeviceAgentCredential, ids["credentials"][ids["stale"]])
        stale_credential.last_used_at = recovery_time

        offline = db.get(Device, ids["offline"])
        offline.status = "online"
        offline.last_seen = recovery_time
        offline_credential = db.get(DeviceAgentCredential, ids["credentials"][ids["offline"]])
        offline_credential.last_used_at = recovery_time

        no_credential = db.get(Device, ids["no_credential"])
        no_credential.status = "online"
        no_credential.last_seen = recovery_time
        no_credential.inventory_data = {"agent_version": "0.20.0", "agent_transport": "modern"}
        db.add(
            DeviceAgentCredential(
                device_id=no_credential.id,
                agent_type="mikrotik_agent",
                secret_hash="e" * 64,
                is_active=True,
                created_at=recovery_time,
                last_used_at=recovery_time,
            )
        )
        db.commit()

    recovered = maintenance_tick(recovery_time)
    assert recovered["agent_changes"] >= 3, recovered
    with SessionLocal() as db:
        assert db.scalar(
            select(func.count(ActionIssue.id)).where(
                ActionIssue.category == "agent_health",
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        ) == 0
        assert db.scalar(
            select(func.count(Notification.id)).where(
                Notification.category == "agent_health",
                Notification.is_active.is_(True),
            )
        ) == 0
        resolved_events = db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.event_type == "AGENT_HEALTH_ISSUE_RESOLVED")
        )
        assert resolved_events >= 3

    resolved_page = client.get(f"/action-center?category=agent_health&status=resolved&customer={ids['customer_id']}")
    assert resolved_page.status_code == 200
    assert "Heartbeat agent MikroTik stale" in resolved_page.text
    assert "Agent MikroTik offline" in resolved_page.text
    assert "Credenziale agent MikroTik assente" in resolved_page.text

    print("Core 0.36 agent health Action Center lifecycle smoke passed")


if __name__ == "__main__":
    main()
