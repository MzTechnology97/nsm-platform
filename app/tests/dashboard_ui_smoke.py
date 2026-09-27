import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "Strong-CI17-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token missing"
    return match.group(1)


def seed():
    with SessionLocal() as db:
        old = db.scalar(select(Customer).where(Customer.code == "CI17"))
        if old:
            db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci17admin"))
        if old_user:
            db.delete(old_user)
        db.commit()

        user = User(
            username="ci17admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI17 Admin",
            role="admin",
            is_active=True,
        )
        customer = Customer(name="CI17 Dashboard Lab", code="CI17")
        db.add_all([user, customer])
        db.flush()
        db.add_all(
            [
                Device(
                    customer_id=customer.id,
                    vendor="mikrotik",
                    device_type="router",
                    name="CI17 Online",
                    display_name="CI17 Online",
                    model="CCR2004",
                    firmware_version="7.20.2",
                    firmware_status="current",
                    management_source="mikrotik_agent",
                    status="online",
                    lifecycle_status="supported",
                ),
                Device(
                    customer_id=customer.id,
                    vendor="ubiquiti",
                    device_type="cpe",
                    name="CI17 Offline",
                    display_name="CI17 Offline",
                    model="PowerBeam 5AC",
                    firmware_version="8.7.19",
                    firmware_status="unknown",
                    management_source="uisp",
                    status="offline",
                    lifecycle_status="supported",
                ),
            ]
        )
        db.commit()


def login(client):
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post(
        "/login",
        data={"username": "ci17admin", "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def main():
    seed()
    anonymous = TestClient(app)
    unauth = anonymous.get("/api/v1/dashboard/summary")
    assert unauth.status_code == 401

    client = TestClient(app)
    login(client)

    dashboard = client.get("/")
    assert dashboard.status_code == 200, dashboard.text
    assert "Copertura operativa" in dashboard.text
    assert "Distribuzione inventario" in dashboard.text
    assert "Auto-refresh" in dashboard.text
    assert "dashboard_live.js" in dashboard.text
    assert "Backup protetti" in dashboard.text
    assert "CVE High / Critical" in dashboard.text

    summary = client.get("/api/v1/dashboard/summary")
    assert summary.status_code == 200, summary.text
    payload = summary.json()
    for key in (
        "customer_count",
        "device_count",
        "online_count",
        "offline_count",
        "backup_protected_count",
        "backup_coverage_percent",
        "firmware_attention_count",
        "severe_cve_device_count",
        "updated_at",
    ):
        assert key in payload, key
    assert payload["device_count"] >= 2
    assert payload["online_count"] >= 1
    assert payload["offline_count"] >= 1

    print("Core 0.17 dynamic dashboard smoke test passed")


if __name__ == "__main__":
    main()
