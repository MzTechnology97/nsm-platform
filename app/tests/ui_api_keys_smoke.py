"""New interface, API key administration: list with review state, create (token once), rotate, revoke, delete, guards."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api_key_models import PlatformApiKey
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Api-Keys-2026-Pass!"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Keys {suffix}", code=f"UK{suffix}", is_active=True)
        db.add(customer)
        db.add_all([
            User(username=f"ci-uk-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-uk-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
        ])
        db.commit()
        customer_id = str(customer.id)

    client, headers = login(f"ci-uk-{suffix}")
    listing = client.get("/api/ui/v1/admin/api-keys")
    assert listing.status_code == 200 and listing.headers["Cache-Control"] == "no-store"
    data = listing.json()
    assert data["scopes"][0] == "inventory.read" and data["expiry_days"] == [30, 90, 365, 0] and data["grace_hours"] == [0, 24, 168]

    assert client.post("/api/ui/v1/admin/api-keys", json={"name": "x"}).status_code == 403, "CSRF header required"
    assert client.post("/api/ui/v1/admin/api-keys", headers=headers, json={"name": " "}).status_code == 400
    assert client.post("/api/ui/v1/admin/api-keys", headers=headers, json={"name": "TEST", "expires_days": 7}).status_code == 400
    assert client.post("/api/ui/v1/admin/api-keys", headers=headers, json={"name": "TEST", "customer_id": "nope"}).status_code == 400
    created = client.post("/api/ui/v1/admin/api-keys", headers=headers, json={
        "name": f"TEST key {suffix}", "customer_id": customer_id, "expires_days": 30, "scopes": ["backup.read", "admin.write"]})
    assert created.status_code == 200 and created.headers["Cache-Control"] == "no-store", created.text
    first = created.json()
    assert first["token"].startswith("nsm_live_") and first["token"].startswith(first["prefix"])
    with SessionLocal() as db:
        stored = db.get(PlatformApiKey, uuid.UUID(first["id"]))
        assert stored.key_hash != first["token"] and first["token"] not in str(stored.__dict__), "token never stored in clear"
        assert stored.scopes == ["inventory.read", "backup.read"], "scopes allow-listed"
    assert TestClient(app).get("/api/v1/public/customers", headers={"X-API-KEY": first["token"]}).status_code == 200

    row = next(k for k in client.get("/api/ui/v1/admin/api-keys").json()["keys"] if k["id"] == first["id"])
    assert row["state"] == "active" and row["customer"]["id"] == customer_id and "token" not in row and row["use_count"] >= 1
    assert row["hints"] == {}, row["hints"]

    base = f"/api/ui/v1/admin/api-keys/{first['id']}"
    assert client.post(f"{base}/rotate", headers=headers, json={"grace_hours": 5}).status_code == 400
    rotated = client.post(f"{base}/rotate", headers=headers, json={"grace_hours": 24})
    assert rotated.status_code == 200 and rotated.headers["Cache-Control"] == "no-store"
    second = rotated.json()
    assert second["id"] != first["id"] and second["token"] != first["token"]
    keys = {k["id"]: k for k in client.get("/api/ui/v1/admin/api-keys").json()["keys"]}
    assert keys[first["id"]]["state"] == "grace" and keys[first["id"]]["hints"]["grace_until"] and keys[first["id"]]["replaced_by_id"] == second["id"]
    assert client.post(f"{base}/rotate", headers=headers, json={"grace_hours": 24}).status_code == 409, "already rotated"

    assert client.post(f"/api/ui/v1/admin/api-keys/{second['id']}/delete", headers=headers).status_code == 409, "revoke before delete"
    assert client.post(f"{base}/revoke", headers=headers).json() == {"revoked": True}
    assert client.post(f"{base}/revoke", headers=headers).json() == {"revoked": False}
    assert TestClient(app).get("/api/v1/public/customers", headers={"X-API-KEY": first["token"]}).status_code == 401
    assert client.post(f"{base}/delete", headers=headers).json() == {"deleted": True}
    assert client.post(f"{base}/revoke", headers=headers).status_code == 404

    with SessionLocal() as db:
        old = PlatformApiKey(name=f"TEST idle {suffix}", key_prefix="nsm_live_ci_idle", key_hash=uuid.uuid4().hex * 2, scopes=["inventory.read"],
                             is_active=True, created_at=utcnow() - timedelta(days=40), expires_at=None)
        db.add(old)
        db.commit()
        idle_id = str(old.id)
    idle = next(k for k in client.get("/api/ui/v1/admin/api-keys").json()["keys"] if k["id"] == idle_id)
    assert idle["hints"]["never_used_days"] >= 40 and idle["hints"]["no_expiry"] is True

    with SessionLocal() as db:
        kinds = dict(db.execute(select(AuditEvent.event_type, func.count(AuditEvent.id)).where(AuditEvent.event_type.like("API_KEY_%"))
                                .group_by(AuditEvent.event_type)).all())
    for kind in ("API_KEY_CREATED", "API_KEY_ROTATED", "API_KEY_REVOKED", "API_KEY_DELETED"):
        assert kinds.get(kind), f"{kind} audited"
    assert client.get("/app/admin/api-keys").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    tech, tech_headers = login(f"ci-uk-tech-{suffix}")
    assert tech.get("/api/ui/v1/admin/api-keys").status_code == 403
    assert tech.post("/api/ui/v1/admin/api-keys", headers=tech_headers, json={"name": "TEST"}).status_code == 403
    print("ui api keys smoke ok")


if __name__ == "__main__":
    main()
