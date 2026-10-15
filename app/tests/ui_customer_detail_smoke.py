"""New interface, customer page API: header, overview counters, backup protection, issues, events, sites, actions, scope."""
import re
import uuid

from fastapi.testclient import TestClient

from app import main as core
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, Customer, Device, Site, User
from app.security import hash_password

PASSWORD = "CI-UI-Customer-Detail-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        mine = Customer(name=f"CI Detail Customer {suffix}", code=f"CD{suffix[:6]}", notes="Cliente di prova")
        other = Customer(name=f"CI Detail Other {suffix}", code=f"CO{suffix[:6]}")
        db.add_all([mine, other])
        db.flush()
        north = Site(customer_id=mine.id, name="Sede Nord", address="Via di prova 1")
        south = Site(customer_id=mine.id, name="Sede Ammiraglia")
        db.add_all([north, south])
        db.flush()
        devices = [
            Device(customer_id=mine.id, site_id=north.id, vendor="mikrotik", device_type="router", name=f"TEST-CD-1-{suffix}", status="online"),
            Device(customer_id=mine.id, site_id=north.id, vendor="mikrotik", device_type="router", name=f"TEST-CD-2-{suffix}", status="offline", lifecycle_status="eol"),
            Device(customer_id=mine.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-CD-3-{suffix}", status="pending_link"),
        ]
        db.add_all(devices)
        db.flush()
        db.add(ActionIssue(category="monitoring", severity="critical", status="open", title="TEST router fermo", customer_id=mine.id, device_id=devices[1].id))
        admin = User(username=f"ci-cd-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        db.add_all([
            admin,
            User(username=f"ci-cd-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-cd-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.flush()
        core.add_event(db, "CUSTOMER_UPDATED", actor=admin, customer_id=mine.id, details={}, source="portal")
        db.commit()
        ids = {"mine": str(mine.id), "other": str(other.id), "north": str(north.id), "issue_device": str(devices[1].id)}

    base = f"/api/ui/v1/customers/{ids['mine']}"
    assert TestClient(app).get(base).status_code == 401
    client = login(f"ci-cd-{suffix}")
    body = client.get(base).json()
    assert body["customer"]["name"] == f"CI Detail Customer {suffix}" and body["customer"]["notes"] == "Cliente di prova"
    assert body["counts"] == {"devices": 3, "sites": 2, "online": 1, "offline": 1, "pending": 1, "unassigned": 1}, body["counts"]
    assert body["metrics"]["alerts"] == 1 and body["metrics"]["lifecycle"] == 1
    assert body["metrics"]["backup"]["devices"] == 3
    assert body["issues"][0]["title"] == "TEST router fermo" and body["issues"][0]["device_id"] == ids["issue_device"]
    assert body["events"][0]["type"] == "CUSTOMER_UPDATED" and body["events"][0]["label"] == "Customer updated"
    assert [s["name"] for s in body["sites"]] == ["Sede Ammiraglia", "Sede Nord"], "sites A to Z"
    north = next(s for s in body["sites"] if s["id"] == ids["north"])
    assert (north["devices"], north["online"], north["offline"], north["address"]) == (2, 1, 1, "Via di prova 1")
    assert [t["key"] for t in body["tabs"]] == ["overview", "devices", "sites", "graphs", "backups", "security", "history"]
    assert body["actions"] == {"edit": True, "add_site": True, "add_device": True, "incidents": True, "uisp_import": True}
    assert client.get(f"/api/ui/v1/customers/{uuid.uuid4()}").status_code == 404

    auditor = login(f"ci-cd-au-{suffix}").get(base).json()
    assert auditor["actions"] == {"edit": False, "add_site": False, "add_device": False, "incidents": True, "uisp_import": False}

    scoped = login(f"ci-cd-scoped-{suffix}")
    assert scoped.get(base).status_code == 404, "another customer's page is invisible"
    assert scoped.get(f"/api/ui/v1/customers/{ids['other']}").status_code == 200
    assert client.get(f"/app/customers/{ids['mine']}/sites").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)
    print("ui customer detail smoke ok")


if __name__ == "__main__":
    main()
