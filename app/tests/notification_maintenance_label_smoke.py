"""Notifications raised during planned maintenance are labelled, not suppressed; the stored title (used to deduplicate) is unchanged."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, MaintenanceWindow, Notification, User, utcnow
from app.notification_models import NotificationDelivery, UserNotificationPreference
from app.security import hash_password

PASSWORD = "CI-Maintenance-Label-2026"


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        first, second = Customer(name=f"CI Maint A {suffix}", code=f"MA{suffix}"), Customer(name=f"CI Maint B {suffix}", code=f"MB{suffix}")
        db.add_all([first, second])
        db.flush()
        in_window = Device(customer_id=first.id, vendor="mikrotik", device_type="router", name=f"TEST-ML-IN-{suffix}", status="offline")
        sibling = Device(customer_id=first.id, vendor="mikrotik", device_type="router", name=f"TEST-ML-SIB-{suffix}", status="offline")
        whole = Device(customer_id=second.id, vendor="mikrotik", device_type="router", name=f"TEST-ML-WHOLE-{suffix}", status="offline")
        db.add_all([in_window, sibling, whole])
        db.flush()
        db.add_all([
            MaintenanceWindow(customer_id=first.id, device_id=in_window.id, starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1), reason="Sostituzione alimentatore"),
            MaintenanceWindow(customer_id=second.id, starts_at=now - timedelta(minutes=30), ends_at=now + timedelta(hours=2), reason="Lavori in sala apparati"),
            # Over: not active any more.
            MaintenanceWindow(customer_id=first.id, starts_at=now - timedelta(days=2), ends_at=now - timedelta(days=1), reason="Finestra chiusa"),
        ])
        user = User(username=f"ci-ml-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True, email=f"ci-ml-{suffix}@example.test")
        db.add(user)
        db.flush()
        db.add(UserNotificationPreference(user_id=user.id, channel="email", enabled=True, min_severity="info", categories=[]))
        db.commit()
        ids = {"in": in_window.id, "sibling": sibling.id, "whole": whole.id, "first": first.id, "second": second.id, "user": user.id}

    title = f"Apparato non raggiungibile {suffix}"
    with SessionLocal() as db:
        rows = {
            "in": Notification(severity="high", category="monitoring", title=f"{title} in", device_id=ids["in"], customer_id=ids["first"]),
            "sibling": Notification(severity="high", category="monitoring", title=f"{title} sibling", device_id=ids["sibling"], customer_id=ids["first"]),
            "whole": Notification(severity="high", category="monitoring", title=f"{title} whole", device_id=ids["whole"]),
            "customer": Notification(severity="warning", category="monitoring", title=f"{title} customer", customer_id=ids["second"]),
            "platform": Notification(severity="warning", category="system", title=f"{title} platform"),
        }
        db.add_all(rows.values())
        db.commit()
        labelled = {key: n.maintenance_window_id is not None for key, n in rows.items()}
        assert labelled == {"in": True, "sibling": False, "whole": True, "customer": True, "platform": False}, labelled
        assert rows["in"].title == f"{title} in", "the stored title is what callers deduplicate on"
        subjects = {d.notification_id: d.subject for d in db.scalars(select(NotificationDelivery).where(NotificationDelivery.user_id == ids["user"]))}
        assert len(subjects) == 5, "nothing is suppressed"
        assert subjects[rows["in"].id].endswith(f"{title} in · in manutenzione programmata"), subjects[rows["in"].id]
        assert "manutenzione" not in subjects[rows["sibling"].id]
        notification_ids = {key: str(n.id) for key, n in rows.items()}

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-ml-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    items = {item["id"]: item for item in client.get("/api/ui/v1/notifications").json()["items"]}
    assert items[notification_ids["in"]]["maintenance"]["reason"] == "Sostituzione alimentatore" and items[notification_ids["in"]]["title"] == f"{title} in"
    assert items[notification_ids["sibling"]]["maintenance"] is None and items[notification_ids["platform"]]["maintenance"] is None
    print("Notification maintenance label smoke passed")


if __name__ == "__main__":
    main()
