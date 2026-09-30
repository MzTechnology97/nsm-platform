import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api_key_models import PlatformApiKey
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, User
from app.security import hash_password

PASSWORD = "CI-ApiKey-Feedback-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token missing"
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(
            username=f"api-feedback-{suffix}",
            password_hash=hash_password(PASSWORD),
            display_name="API Feedback Test",
            role="admin",
            is_active=True,
        )
        customer = Customer(
            name=f"API Feedback Customer {suffix}",
            code=f"AF{suffix[:6]}",
        )
        db.add_all([user, customer])
        db.commit()
        return user.username, customer.id


def login(client: TestClient, username: str):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": PASSWORD, "csrf": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def main():
    username, customer_id = seed()
    client = TestClient(app)
    login(client, username)

    page = client.get("/admin/api-keys")
    assert page.status_code == 200
    csrf = csrf_from(page.text)

    # Normal browser validation must stay inside the administration GUI.
    blank = client.post(
        "/admin/api-keys",
        data={"name": "", "customer_id": "", "expires_days": "90", "csrf": csrf},
        follow_redirects=False,
    )
    assert blank.status_code == 303
    assert blank.headers["location"] == "/admin/api-keys"
    assert not blank.headers.get("content-type", "").startswith("application/json")
    feedback = client.get(blank.headers["location"])
    assert "Nome API key obbligatorio" in feedback.text
    assert "flash-warning" in feedback.text

    page = client.get("/admin/api-keys")
    csrf = csrf_from(page.text)
    invalid_customer = client.post(
        "/admin/api-keys",
        data={
            "name": "CI invalid customer",
            "customer_id": "not-a-uuid",
            "expires_days": "90",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert invalid_customer.status_code == 303
    invalid_feedback = client.get(invalid_customer.headers["location"])
    assert "Cliente non valido" in invalid_feedback.text
    assert "flash-warning" in invalid_feedback.text

    # Valid creation must remain inline because the raw token is shown once.
    page = client.get("/admin/api-keys")
    csrf = csrf_from(page.text)
    created = client.post(
        "/admin/api-keys",
        data={
            "name": "CI feedback key",
            "customer_id": str(customer_id),
            "expires_days": "30",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert created.status_code == 200
    assert "MOSTRATA UNA VOLTA" in created.text
    assert re.search(r"nsm_live_[A-Za-z0-9_-]+", created.text)

    with SessionLocal() as db:
        key = db.scalar(select(PlatformApiKey).where(PlatformApiKey.name == "CI feedback key"))
        assert key and key.is_active
        key_id = key.id

    # Active keys cannot be deleted; this is an expected GUI warning, not JSON.
    page = client.get("/admin/api-keys")
    csrf = csrf_from(page.text)
    active_delete = client.post(
        f"/admin/api-keys/{key_id}/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert active_delete.status_code == 303
    assert active_delete.headers["location"] == "/admin/api-keys"
    active_feedback = client.get(active_delete.headers["location"])
    assert "Revoca la API key prima di eliminarla" in active_feedback.text
    assert "flash-warning" in active_feedback.text

    page = client.get("/admin/api-keys")
    csrf = csrf_from(page.text)
    revoked = client.post(
        f"/admin/api-keys/{key_id}/revoke",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert revoked.status_code == 303
    revoked_feedback = client.get(revoked.headers["location"])
    assert "API key revocata" in revoked_feedback.text
    assert "flash-success" in revoked_feedback.text

    page = client.get("/admin/api-keys")
    csrf = csrf_from(page.text)
    deleted = client.post(
        f"/admin/api-keys/{key_id}/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    deleted_feedback = client.get(deleted.headers["location"])
    assert "API key eliminata" in deleted_feedback.text
    assert "flash-success" in deleted_feedback.text

    # A stale browser action should remain contextual.
    page = client.get("/admin/api-keys")
    csrf = csrf_from(page.text)
    stale = client.post(
        f"/admin/api-keys/{key_id}/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert stale.status_code == 303
    stale_feedback = client.get(stale.headers["location"])
    assert "API key non trovata" in stale_feedback.text
    assert "flash-warning" in stale_feedback.text

    # Machine-facing API behavior deliberately remains JSON/HTTP based.
    machine = client.get("/api/v1/public/devices")
    assert machine.status_code == 401
    assert machine.headers.get("content-type", "").startswith("application/json")

    print("API key contextual browser feedback smoke passed")


if __name__ == "__main__":
    main()
