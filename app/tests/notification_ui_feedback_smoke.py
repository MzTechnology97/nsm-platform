import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Notification, NotificationRead, User
from app.security import hash_password

PASSWORD = "Strong-CI-Notification-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token missing"
    return match.group(1)


def seed():
    with SessionLocal() as db:
        user = User(
            username="ci-notification-admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI Notification Admin",
            role="admin",
            is_active=True,
        )
        customer = Customer(name="CI Notification Customer", code="CINOTIFY")
        db.add_all([user, customer])
        db.flush()
        first = Notification(
            severity="warning",
            category="test",
            title="CI synthetic notification one",
            message="Synthetic notification fixture.",
            customer_id=customer.id,
            source_url="/notifications",
            is_active=True,
        )
        second = Notification(
            severity="info",
            category="test",
            title="CI synthetic notification two",
            message="Synthetic notification fixture two.",
            customer_id=customer.id,
            source_url="/notifications",
            is_active=True,
        )
        db.add_all([first, second])
        db.commit()
        return user.id, first.id, second.id


def login(client: TestClient) -> None:
    page = client.get("/login")
    assert page.status_code == 200
    response = client.post(
        "/login",
        data={
            "username": "ci-notification-admin",
            "password": PASSWORD,
            "csrf": csrf_from(page.text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def main():
    user_id, first_id, second_id = seed()
    client = TestClient(app)
    login(client)

    single_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/notifications/{notification_id}/read"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    all_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/notifications/read-all"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert len(single_routes) == 1
    assert single_routes[0].name == "notification_read_ui_feedback"
    assert len(all_routes) == 1
    assert all_routes[0].name == "notifications_read_all_ui_feedback"

    page = client.get("/notifications")
    assert page.status_code == 200
    first = client.post(
        f"/notifications/{first_id}/read",
        data={"csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert first.status_code == 303
    assert first.headers["location"] == "/notifications"
    feedback = client.get("/notifications")
    assert "flash-success" in feedback.text
    assert "Notifica aggiornata" in feedback.text
    assert "application/json" not in feedback.headers.get("content-type", "")
    assert "flash-success" not in client.get("/notifications").text

    with SessionLocal() as db:
        assert db.scalar(
            select(NotificationRead).where(
                NotificationRead.notification_id == first_id,
                NotificationRead.user_id == user_id,
            )
        ) is not None

    stale_id = uuid.UUID("00000000-0000-4000-8000-000000000103")
    page = client.get("/notifications")
    stale = client.post(
        f"/notifications/{stale_id}/read",
        data={"csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert stale.status_code == 303
    assert stale.headers["location"] == "/notifications"
    stale_feedback = client.get("/notifications")
    assert "flash-warning" in stale_feedback.text
    assert "Notifica non disponibile" in stale_feedback.text

    page = client.get("/notifications")
    read_all = client.post(
        "/notifications/read-all",
        data={"csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert read_all.status_code == 303
    assert read_all.headers["location"] == "/notifications"
    all_feedback = client.get("/notifications")
    assert "flash-success" in all_feedback.text
    assert "Notifiche aggiornate" in all_feedback.text

    with SessionLocal() as db:
        read_count = db.scalar(
            select(func.count(NotificationRead.id)).where(NotificationRead.user_id == user_id)
        )
        assert read_count == 2
        assert db.scalar(
            select(NotificationRead).where(
                NotificationRead.notification_id == second_id,
                NotificationRead.user_id == user_id,
            )
        ) is not None

    print("Notification Center contextual read feedback smoke passed")


if __name__ == "__main__":
    main()
