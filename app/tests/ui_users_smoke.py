"""New interface, user administration API: list, create, role, enable, customer scope, sessions, second factor, guards."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Users-2026-Pass!"
NEW_PASSWORD = "CI-UI-Users-New-2026-Pass!"


def login(username, password=PASSWORD):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    response = client.post("/login", data={"username": username, "password": password, "csrf": token}, follow_redirects=False)
    assert response.status_code == 303, response.status_code
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        alpha = Customer(name=f"CI UI Users A {suffix}", code=f"UA{suffix}")
        beta = Customer(name=f"CI UI Users B {suffix}", code=f"UB{suffix}")
        db.add_all([alpha, beta])
        admin = User(username=f"ci-uu-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        tech = User(username=f"ci-uu-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        db.add_all([admin, tech])
        db.commit()
        ids = {"alpha": str(alpha.id), "beta": str(beta.id), "admin": str(admin.id), "tech": str(tech.id)}

    client, headers = login(f"ci-uu-{suffix}")
    data = client.get("/api/ui/v1/admin/users").json()
    assert data["roles"] == ["admin", "technician", "operator", "auditor"] and data["limits"] == {"pair": 5, "ip": 30}
    users = {row["id"]: row for row in data["users"]}
    assert users[ids["admin"]]["is_self"] is True and users[ids["tech"]]["two_factor"] is False and users[ids["tech"]]["scope"] == []
    assert {"id": ids["alpha"], "name": f"CI UI Users A {suffix}"} in data["customers"]

    assert client.post("/api/ui/v1/admin/users", json={"username": "x"}).status_code == 403, "CSRF header required"
    weak = client.post("/api/ui/v1/admin/users", headers=headers, json={"username": f"ci-uu-new-{suffix}", "role": "operator", "password": "short"})
    assert weak.status_code == 400
    assert client.post("/api/ui/v1/admin/users", headers=headers, json={"username": f"ci-uu-new-{suffix}", "role": "root", "password": NEW_PASSWORD}).status_code == 400
    created = client.post("/api/ui/v1/admin/users", headers=headers, json={
        "username": f"ci-uu-new-{suffix}", "display_name": "CI New", "email": "new@example.test", "role": "operator", "password": NEW_PASSWORD})
    assert created.status_code == 200, created.text
    new_id = created.json()["id"]
    duplicate = client.post("/api/ui/v1/admin/users", headers=headers, json={"username": f"ci-uu-new-{suffix}", "role": "operator", "password": NEW_PASSWORD})
    assert duplicate.status_code == 409
    login(f"ci-uu-new-{suffix}", NEW_PASSWORD)

    base = f"/api/ui/v1/admin/users/{new_id}"
    assert client.post(f"{base}/role", headers=headers, json={"role": "auditor"}).json() == {"role": "auditor"}
    assert client.post(f"{base}/role", headers=headers, json={"role": "root"}).status_code == 400
    assert client.post(f"/api/ui/v1/admin/users/{ids['admin']}/role", headers=headers, json={"role": "operator"}).status_code == 400, "no self-demotion"
    assert client.post(f"{base}/scope", headers=headers, json={"customer_ids": [ids["beta"], "not-a-customer", ids["alpha"]]}).json() == {
        "scope": sorted([ids["alpha"], ids["beta"]])}
    row = next(u for u in client.get("/api/ui/v1/admin/users").json()["users"] if u["id"] == new_id)
    assert {item["id"] for item in row["scope"]} == {ids["alpha"], ids["beta"]} and row["role"] == "auditor"
    assert client.post(f"{base}/scope", headers=headers, json={"customer_ids": []}).json() == {"scope": []}

    assert client.post(f"{base}/toggle", headers=headers).json() == {"active": False}
    assert client.post(f"{base}/toggle", headers=headers).json() == {"active": True}
    assert client.post(f"/api/ui/v1/admin/users/{ids['admin']}/toggle", headers=headers).status_code == 400, "cannot disable yourself"

    tech_client, _ = login(f"ci-uu-tech-{suffix}")
    assert tech_client.get("/api/ui/v1/admin/users").status_code == 403, "users.manage required"
    assert client.post(f"/api/ui/v1/admin/users/{ids['tech']}/sessions/revoke", headers=headers).json() == {"revoked": True}
    assert tech_client.get("/api/ui/v1/session").status_code == 401, "revoked session no longer valid"
    assert client.post(f"/api/ui/v1/admin/users/{ids['admin']}/sessions/revoke", headers=headers).status_code == 400
    with SessionLocal() as db:
        target = db.get(User, uuid.UUID(ids["tech"]))
        target.totp_enabled_at, target.totp_secret_encrypted = utcnow(), "ci-placeholder"
        db.commit()
    assert next(u for u in client.get("/api/ui/v1/admin/users").json()["users"] if u["id"] == ids["tech"])["two_factor"] is True
    assert client.post(f"/api/ui/v1/admin/users/{ids['tech']}/2fa/reset", headers=headers).json() == {"two_factor": False}
    with SessionLocal() as db:
        assert db.get(User, uuid.UUID(ids["tech"])).totp_enabled_at is None
    assert client.post(f"/api/ui/v1/admin/users/{uuid.uuid4()}/toggle", headers=headers).status_code == 404

    with SessionLocal() as db:
        kinds = dict(db.execute(select(AuditEvent.event_type, func.count(AuditEvent.id)).where(AuditEvent.event_type.like("USER_%"))
                                .group_by(AuditEvent.event_type)).all())
    for kind in ("USER_CREATED", "USER_ROLE_CHANGED", "USER_CUSTOMER_SCOPE_CHANGED", "USER_STATUS_CHANGED", "USER_SESSIONS_REVOKED", "USER_2FA_RESET"):
        assert kinds.get(kind), f"{kind} audited"
    assert client.get("/app/admin/users").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)
    print("ui users smoke ok")


if __name__ == "__main__":
    main()
