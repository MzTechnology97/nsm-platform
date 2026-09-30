import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "test-only-device-crud-feedback"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "csrf token missing"
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"device-crud-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="Device CRUD Feedback Test",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"Synthetic Customer {suffix}", code=f"DCF{suffix[:5]}")
        other_customer = Customer(name=f"Synthetic Other {suffix}", code=f"DCO{suffix[:5]}")
        db.add_all([user, customer, other_customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="Synthetic Router",
            display_name="Synthetic Router",
            device_identity=f"TEST-ROUTER-{suffix}",
            model="TEST-MODEL",
            firmware_version="TEST-7.20.7",
            status="online",
        )
        db.add(device)
        db.commit()
        return user.username, customer.id, other_customer.id, device.id


def login(client: TestClient, username: str) -> None:
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": _csrf(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def main():
    username, customer_id, other_customer_id, device_id = seed()
    client = TestClient(app)
    login(client, username)

    manage_page = client.get(f"/devices/{device_id}/manage")
    assert manage_page.status_code == 200, manage_page.text
    token = _csrf(manage_page.text)

    invalid_customer = client.post(
        f"/devices/{device_id}/manage",
        data={
            "display_name": "Synthetic Router",
            "customer_id": "not-a-uuid",
            "site_id": "",
            "csrf": token,
        },
        follow_redirects=False,
    )
    assert invalid_customer.status_code == 303
    assert invalid_customer.headers["location"] == f"/devices/{device_id}/manage"
    assert not invalid_customer.headers.get("content-type", "").startswith("application/json")
    feedback = client.get(invalid_customer.headers["location"])
    assert feedback.status_code == 200
    assert "Cliente non valido" in feedback.text
    assert "flash-warning" in feedback.text

    token = _csrf(feedback.text)
    valid_manage = client.post(
        f"/devices/{device_id}/manage",
        data={
            "display_name": "Synthetic Router Updated",
            "customer_id": str(other_customer_id),
            "site_id": "",
            "csrf": token,
        },
        follow_redirects=False,
    )
    assert valid_manage.status_code == 303
    assert valid_manage.headers["location"] == f"/devices/{device_id}"
    success = client.get(valid_manage.headers["location"])
    assert success.status_code == 200
    assert "Apparato aggiornato" in success.text
    assert "flash-success" in success.text

    manage_page = client.get(f"/devices/{device_id}/manage")
    token = _csrf(manage_page.text)
    invalid_delete = client.post(
        f"/devices/{device_id}/delete",
        data={"confirm": "NO", "csrf": token},
        follow_redirects=False,
    )
    assert invalid_delete.status_code == 303
    assert invalid_delete.headers["location"] == f"/devices/{device_id}/manage"
    assert not invalid_delete.headers.get("content-type", "").startswith("application/json")
    delete_feedback = client.get(invalid_delete.headers["location"])
    assert delete_feedback.status_code == 200
    assert "Conferma eliminazione non valida" in delete_feedback.text
    assert "flash-warning" in delete_feedback.text

    missing_id = uuid.uuid4()
    token = _csrf(delete_feedback.text)
    missing = client.post(
        f"/devices/{missing_id}/manage",
        data={
            "display_name": "Missing",
            "customer_id": str(customer_id),
            "site_id": "",
            "csrf": token,
        },
        follow_redirects=False,
    )
    assert missing.status_code == 303
    assert missing.headers["location"] == "/devices"
    assert not missing.headers.get("content-type", "").startswith("application/json")
    missing_feedback = client.get(missing.headers["location"])
    assert missing_feedback.status_code == 200
    assert "Apparato non trovato" in missing_feedback.text
    assert "flash-error" in missing_feedback.text

    print("Device CRUD contextual feedback smoke passed")


if __name__ == "__main__":
    main()
