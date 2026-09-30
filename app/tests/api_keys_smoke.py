import hashlib
import re
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api_key_models import PlatformApiKey
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "Strong-CI23-Password-2026"
TECH_PASSWORD = "Strong-CI23-Tech-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token missing"
    return match.group(1)


def login(client, username="ci23admin", password=PASSWORD):
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def seed():
    with SessionLocal() as db:
        for code in ("CI23A", "CI23B"):
            old = db.scalar(select(Customer).where(Customer.code == code))
            if old:
                db.delete(old)
        for username in ("ci23admin", "ci23tech"):
            old_user = db.scalar(select(User).where(User.username == username))
            if old_user:
                db.delete(old_user)
        db.commit()

        admin = User(
            username="ci23admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI23 Admin",
            role="admin",
            is_active=True,
        )
        tech = User(
            username="ci23tech",
            password_hash=hash_password(TECH_PASSWORD),
            display_name="CI23 Technician",
            role="technician",
            is_active=True,
        )
        customer_a = Customer(name="CI23 Customer A", code="CI23A")
        customer_b = Customer(name="CI23 Customer B", code="CI23B")
        db.add_all([admin, tech, customer_a, customer_b])
        db.flush()
        device_a = Device(
            customer_id=customer_a.id,
            vendor="mikrotik",
            device_type="router",
            name="CI23-A-Router",
            display_name="CI23 A Router",
            serial_number="CI23-A-SERIAL",
            primary_mac="02:23:00:00:00:01",
            management_ip="192.0.2.31",
            firmware_version="7.20.2",
            status="online",
        )
        device_b = Device(
            customer_id=customer_b.id,
            vendor="ubiquiti",
            device_type="cpe",
            name="CI23-B-Radio",
            display_name="CI23 B Radio",
            serial_number="CI23-B-SERIAL",
            primary_mac="02:23:00:00:00:02",
            management_ip="192.0.2.32",
            firmware_version="8.7.19",
            status="online",
        )
        db.add_all([device_a, device_b])
        db.commit()
        return customer_a.id, customer_b.id, device_a.id, device_b.id


def create_key(client, name, customer_id="", expires_days=90):
    page = client.get("/admin/api-keys")
    assert page.status_code == 200, page.text
    csrf = csrf_from(page.text)
    response = client.post(
        "/admin/api-keys",
        data={
            "csrf": csrf,
            "name": name,
            "customer_id": str(customer_id) if customer_id else "",
            "expires_days": str(expires_days),
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers.get("cache-control") == "no-store"
    match = re.search(r"(nsm_live_[A-Za-z0-9_-]+)", response.text)
    assert match, response.text
    token = match.group(1)
    assert "MOSTRATA UNA VOLTA" in response.text
    return token


def main():
    customer_a, customer_b, device_a, device_b = seed()
    client = TestClient(app)
    login(client)

    page = client.get("/admin/api-keys")
    assert page.status_code == 200
    assert "X-API-KEY" in page.text and "Authorization: Bearer" in page.text
    assert "inventory.read" in page.text

    global_token = create_key(client, "CI23 Global")
    with SessionLocal() as db:
        row = db.scalar(select(PlatformApiKey).where(PlatformApiKey.name == "CI23 Global"))
        assert row
        assert row.key_hash == hashlib.sha256(global_token.encode()).hexdigest()
        assert global_token != row.key_hash
        assert row.key_prefix == global_token[:20]
        assert row.scopes == ["inventory.read"]
        assert row.customer_id is None
        global_key_id = row.id

    no_key = client.get("/api/v1/public/devices?q=CI23-")
    assert no_key.status_code == 401

    header_devices = client.get(
        "/api/v1/public/devices?q=CI23-",
        headers={"X-API-KEY": global_token},
    )
    assert header_devices.status_code == 200, header_devices.text
    names = {item["name"] for item in header_devices.json()["items"]}
    assert {"CI23-A-Router", "CI23-B-Radio"}.issubset(names)

    bearer_devices = client.get(
        "/api/v1/public/devices?q=CI23-&limit=1000",
        headers={"Authorization": f"Bearer {global_token}"},
    )
    assert bearer_devices.status_code == 200
    assert bearer_devices.json()["limit"] == 100

    customers = client.get(
        "/api/v1/public/customers",
        headers={"X-API-KEY": global_token},
    )
    assert customers.status_code == 200
    codes = {item["code"] for item in customers.json()["items"]}
    assert {"CI23A", "CI23B"}.issubset(codes)

    detail = client.get(
        f"/api/v1/public/devices/{device_a}",
        headers={"X-API-KEY": global_token},
    )
    assert detail.status_code == 200
    assert detail.json()["serial_number"] == "CI23-A-SERIAL"
    assert "inventory_data" not in detail.json()

    conflicting = client.get(
        "/api/v1/public/devices",
        headers={
            "X-API-KEY": global_token,
            "Authorization": "Bearer nsm_live_not-the-same",
        },
    )
    assert conflicting.status_code == 401

    invalid = client.get(
        "/api/v1/public/devices",
        headers={"X-API-KEY": "nsm_live_invalid"},
    )
    assert invalid.status_code == 401

    scoped_token = create_key(client, "CI23 Customer A", customer_a, 30)
    scoped = client.get(
        "/api/v1/public/devices?q=CI23-",
        headers={"Authorization": f"Bearer {scoped_token}"},
    )
    assert scoped.status_code == 200
    scoped_names = {item["name"] for item in scoped.json()["items"]}
    assert "CI23-A-Router" in scoped_names
    assert "CI23-B-Radio" not in scoped_names

    forbidden_customer = client.get(
        f"/api/v1/public/devices?customer_id={customer_b}",
        headers={"X-API-KEY": scoped_token},
    )
    assert forbidden_customer.status_code == 403

    hidden_detail = client.get(
        f"/api/v1/public/devices/{device_b}",
        headers={"X-API-KEY": scoped_token},
    )
    assert hidden_detail.status_code == 404

    scoped_customers = client.get(
        "/api/v1/public/customers",
        headers={"X-API-KEY": scoped_token},
    )
    assert scoped_customers.status_code == 200
    assert [item["code"] for item in scoped_customers.json()["items"]] == ["CI23A"]

    with SessionLocal() as db:
        scoped_row = db.scalar(select(PlatformApiKey).where(PlatformApiKey.name == "CI23 Customer A"))
        assert scoped_row and scoped_row.last_used_at is not None
        scoped_key_id = scoped_row.id

    admin_page = client.get("/admin/api-keys")
    csrf = csrf_from(admin_page.text)
    revoke = client.post(
        f"/admin/api-keys/{scoped_key_id}/revoke",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert revoke.status_code == 303
    revoked_call = client.get(
        "/api/v1/public/devices",
        headers={"X-API-KEY": scoped_token},
    )
    assert revoked_call.status_code == 401

    admin_page = client.get("/admin/api-keys")
    csrf = csrf_from(admin_page.text)
    deleted = client.post(
        f"/admin/api-keys/{scoped_key_id}/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    with SessionLocal() as db:
        assert db.get(PlatformApiKey, scoped_key_id) is None

    admin_page = client.get("/admin/api-keys")
    csrf = csrf_from(admin_page.text)
    active_delete = client.post(
        f"/admin/api-keys/{global_key_id}/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert active_delete.status_code == 303
    assert active_delete.headers["location"] == "/admin/api-keys"
    assert not active_delete.headers.get("content-type", "").startswith("application/json")
    active_feedback = client.get(active_delete.headers["location"])
    assert "Revoca la API key prima di eliminarla" in active_feedback.text
    assert "flash-warning" in active_feedback.text
    with SessionLocal() as db:
        assert db.get(PlatformApiKey, global_key_id).is_active is True

    with SessionLocal() as db:
        global_row = db.get(PlatformApiKey, global_key_id)
        global_row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    expired = client.get(
        "/api/v1/public/devices",
        headers={"X-API-KEY": global_token},
    )
    assert expired.status_code == 401

    tech = TestClient(app)
    login(tech, "ci23tech", TECH_PASSWORD)
    forbidden = tech.get("/admin/api-keys")
    assert forbidden.status_code == 403
    session_is_not_api_auth = tech.get("/api/v1/public/devices")
    assert session_is_not_api_auth.status_code == 401

    print("Core 0.23 API key management and isolation smoke test passed")


if __name__ == "__main__":
    main()
