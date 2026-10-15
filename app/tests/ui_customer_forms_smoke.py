"""New interface, customer and site forms: create, update, delete with the classic validation, audit, permissions, scope, CSRF."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, Site, User
from app.security import hash_password

PASSWORD = "CI-UI-Customer-Forms-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def code(response):
    assert response.status_code == 422, response.text
    return response.json()["detail"]["code"]


def events(kind, customer_id):
    with SessionLocal() as db:
        return len(list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == kind, AuditEvent.customer_id == uuid.UUID(customer_id)))))


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        other = Customer(name=f"CI UI Forms other {suffix}", code=f"UO{suffix}")
        db.add(other)
        db.flush()
        db.add_all([
            User(username=f"ci-uf-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
            User(username=f"ci-uf-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-uf-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[]),
        ])
        db.commit()
        other_id = str(other.id)

    client, csrf = login(f"ci-uf-{suffix}")
    auditor, auditor_csrf = login(f"ci-uf-aud-{suffix}")

    # Create.
    assert client.post("/api/ui/v1/customers", json={"name": "x"}).status_code == 403, "CSRF header required"
    assert auditor.post("/api/ui/v1/customers", headers=auditor_csrf, json={"name": "x"}).status_code == 403, "customers.write required"
    assert code(client.post("/api/ui/v1/customers", headers=csrf, json={"name": "   "})) == "name_required"
    created = client.post("/api/ui/v1/customers", headers=csrf, json={"name": f"  CI UI Forms {suffix} ", "code": f"UF{suffix}", "notes": "synthetic"})
    assert created.status_code == 200, created.text
    customer_id = created.json()["id"]
    assert code(client.post("/api/ui/v1/customers", headers=csrf, json={"name": "dup", "code": f"UO{suffix}"})) == "code_taken"
    detail = client.get(f"/api/ui/v1/customers/{customer_id}").json()
    assert detail["customer"]["name"] == f"CI UI Forms {suffix}" and detail["customer"]["code"] == f"UF{suffix}" and detail["customer"]["notes"] == "synthetic"
    assert events("CUSTOMER_ADDED", customer_id) == 1

    # Update.
    assert code(client.post(f"/api/ui/v1/customers/{customer_id}", headers=csrf, json={"name": ""})) == "name_required"
    assert code(client.post(f"/api/ui/v1/customers/{customer_id}", headers=csrf, json={"name": "renamed", "code": f"UO{suffix}"})) == "code_taken"
    assert client.post(f"/api/ui/v1/customers/{customer_id}", headers=csrf, json={"name": f"CI UI Forms renamed {suffix}", "code": "", "notes": ""}).status_code == 200
    detail = client.get(f"/api/ui/v1/customers/{customer_id}").json()
    assert detail["customer"]["name"] == f"CI UI Forms renamed {suffix}" and detail["customer"]["code"] is None and detail["customer"]["notes"] is None
    assert events("CUSTOMER_UPDATED", customer_id) == 1
    assert client.post(f"/api/ui/v1/customers/{uuid.uuid4()}", headers=csrf, json={"name": "x"}).status_code == 404

    # Sites.
    assert code(client.post(f"/api/ui/v1/customers/{customer_id}/sites", headers=csrf, json={"name": ""})) == "name_required"
    site_id = client.post(f"/api/ui/v1/customers/{customer_id}/sites", headers=csrf, json={"name": "CI site", "address": "Via Esempio 1"}).json()["id"]
    assert client.post(f"/api/ui/v1/customers/{uuid.uuid4()}/sites", headers=csrf, json={"name": "x"}).status_code == 404
    assert client.post(f"/api/ui/v1/customers/{other_id}/sites/{site_id}", headers=csrf, json={"name": "x"}).status_code == 404, "site of another customer"
    assert code(client.post(f"/api/ui/v1/customers/{customer_id}/sites/{site_id}", headers=csrf, json={"name": " "})) == "name_required"
    assert client.post(f"/api/ui/v1/customers/{customer_id}/sites/{site_id}", headers=csrf, json={"name": "CI site 2", "address": "", "notes": "n"}).status_code == 200
    site = next(s for s in client.get(f"/api/ui/v1/customers/{customer_id}").json()["sites"] if s["id"] == site_id)
    assert site["name"] == "CI site 2" and site["address"] is None and site["notes"] == "n"
    with SessionLocal() as db:
        device = Device(customer_id=uuid.UUID(customer_id), site_id=uuid.UUID(site_id), vendor="mikrotik", device_type="router",
                        name=f"TEST-UF-{suffix}", display_name=f"TEST-UF-{suffix}", status="online")
        db.add(device)
        db.commit()
        device_id = device.id
    assert client.post(f"/api/ui/v1/customers/{customer_id}/sites/{site_id}/delete", headers=csrf).json() == {"moved": 1}
    with SessionLocal() as db:
        assert db.get(Device, device_id).site_id is None and db.get(Site, uuid.UUID(site_id)) is None
    assert events("SITE_DELETED", customer_id) == 1 and events("DEVICE_SITE_CHANGED", customer_id) == 1

    # Scope: a user limited to no customer cannot touch this one.
    scoped, scoped_csrf = login(f"ci-uf-scoped-{suffix}")
    with SessionLocal() as db:
        db.scalar(select(User).where(User.username == f"ci-uf-scoped-{suffix}")).customer_scope = [other_id]
        db.commit()
    assert scoped.post(f"/api/ui/v1/customers/{customer_id}", headers=scoped_csrf, json={"name": "x"}).status_code == 404

    # Delete: the typed name must match.
    assert code(client.post(f"/api/ui/v1/customers/{customer_id}/delete", headers=csrf, json={"confirm_name": "wrong"})) == "confirm_name"
    assert client.get(f"/api/ui/v1/customers/{customer_id}").status_code == 200
    deleted = client.post(f"/api/ui/v1/customers/{customer_id}/delete", headers=csrf, json={"confirm_name": f"CI UI Forms renamed {suffix}"})
    assert deleted.json() == {"deleted": True}
    assert client.get(f"/api/ui/v1/customers/{customer_id}").status_code == 404
    with SessionLocal() as db:
        assert db.get(Device, device_id) is None
        snapshots = [e for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type == "CUSTOMER_DELETED"))
                     if (e.details or {}).get("customer_id") == customer_id]
        assert len(snapshots) == 1 and snapshots[0].details["device_count"] == 1

    # The classic forms still work on the shared functions.
    page = client.get("/customers/new/form")
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    assert client.post("/customers", data={"name": f"CI classic {suffix}", "code": "", "notes": "", "csrf": token}, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        classic = db.scalar(select(Customer).where(Customer.name == f"CI classic {suffix}"))
        assert classic is not None
    assert client.post(f"/customers/{classic.id}/sites", data={"name": "classic site", "csrf": token}, follow_redirects=False).status_code == 303

    for path in ("/app/customers/new", f"/app/customers/{other_id}/edit"):
        assert client.get(path).status_code in (200, 503), path
    print("ui customer forms smoke ok")


if __name__ == "__main__":
    main()
