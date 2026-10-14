"""New interface, customer list API: health counters, filters, sorting, permissions and customer scope."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, Customer, Device, DeviceVulnerability, SecurityAdvisory, Site, User
from app.security import hash_password

PASSWORD = "CI-UI-Customers-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        alpha = Customer(name=f"CI Alpha {suffix}", code=f"CA{suffix[:6]}")
        beta = Customer(name=f"CI Beta {suffix}", code=f"CB{suffix[:6]}")
        gamma = Customer(name=f"CI Gamma {suffix}", code=f"CG{suffix[:6]}", is_active=False)
        db.add_all([alpha, beta, gamma])
        db.flush()
        db.add_all([Site(customer_id=alpha.id, name="Sede A1"), Site(customer_id=alpha.id, name="Sede A2"), Site(customer_id=beta.id, name="Sede B1")])
        a1 = Device(customer_id=alpha.id, vendor="mikrotik", device_type="router", name=f"TEST-CA-1-{suffix}", status="online")
        a2 = Device(customer_id=alpha.id, vendor="mikrotik", device_type="router", name=f"TEST-CA-2-{suffix}", status="offline",
                    firmware_version="7.12.1", recommended_firmware_version="7.19.4")
        a3 = Device(customer_id=alpha.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-CA-3-{suffix}", status="online")
        b1 = Device(customer_id=beta.id, vendor="mikrotik", device_type="router", name=f"TEST-CB-1-{suffix}", status="online")
        db.add_all([a1, a2, a3, b1])
        db.flush()
        advisory = SecurityAdvisory(cve_id=f"CVE-2099-{int(suffix, 16) % 90000 + 10000}", source="manual", severity="critical")
        db.add(advisory)
        db.flush()
        db.add(DeviceVulnerability(device_id=b1.id, advisory_id=advisory.id, status="open"))
        db.add_all([
            ActionIssue(category="backup", severity="warning", status="open", title="TEST backup", customer_id=alpha.id, device_id=a1.id),
            ActionIssue(category="monitoring", severity="critical", status="open", title="TEST down", customer_id=alpha.id, device_id=a2.id),
            ActionIssue(category="monitoring", severity="warning", status="resolved", title="TEST old", customer_id=beta.id, device_id=b1.id),
        ])
        db.add_all([
            User(username=f"ci-cu-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-cu-op-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True),
            User(username=f"ci-cu-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(beta.id)]),
        ])
        db.commit()
        ids = {"alpha": str(alpha.id), "beta": str(beta.id), "gamma": str(gamma.id)}

    assert TestClient(app).get("/api/ui/v1/customers").status_code == 401
    admin = login(f"ci-cu-{suffix}")
    body = admin.get(f"/api/ui/v1/customers?q={suffix}").json()
    assert body["total"] == 3 and body["can_create"]
    rows = {row["id"]: row for row in body["items"]}
    alpha = rows[ids["alpha"]]
    assert (alpha["devices"], alpha["online"], alpha["offline"], alpha["sites"]) == (3, 2, 1, 2), alpha
    assert alpha["updates"] == 1 and alpha["severe"] == 0 and alpha["alerts"] == 2 and alpha["backup_alerts"] == 1
    beta = rows[ids["beta"]]
    assert beta["severe"] == 1 and beta["alerts"] == 0, "resolved issues are not open alerts"
    assert rows[ids["gamma"]]["active"] is False and rows[ids["gamma"]]["devices"] == 0
    assert body["counts"] == {"all": 3, "alerts": 1, "severe": 1, "updates": 1, "offline": 1}
    assert [r["name"] for r in body["items"]] == sorted(r["name"] for r in body["items"]), "names A to Z by default"

    assert [r["id"] for r in admin.get(f"/api/ui/v1/customers?q={suffix}&filter=severe").json()["items"]] == [ids["beta"]]
    filtered = admin.get(f"/api/ui/v1/customers?q={suffix}&filter=offline").json()
    assert [r["id"] for r in filtered["items"]] == [ids["alpha"]] and filtered["counts"]["all"] == 3, "counts ignore the active filter"
    by_devices = admin.get(f"/api/ui/v1/customers?q={suffix}&sort=devices").json()["items"]
    assert [r["id"] for r in by_devices] == [ids["alpha"], ids["beta"], ids["gamma"]], "counters sort largest first"
    ascending = admin.get(f"/api/ui/v1/customers?q={suffix}&sort=devices&dir=asc").json()["items"]
    assert ascending[0]["id"] == ids["gamma"]
    assert admin.get(f"/api/ui/v1/customers?q=CB{suffix[:6]}").json()["total"] == 1, "search matches the code"
    assert admin.get(f"/api/ui/v1/customers?q={suffix}&filter=bogus&sort=bogus").json()["total"] == 3

    assert login(f"ci-cu-op-{suffix}").get(f"/api/ui/v1/customers?q={suffix}").json()["can_create"] is False
    scoped = login(f"ci-cu-scoped-{suffix}").get(f"/api/ui/v1/customers?q={suffix}").json()
    assert [r["id"] for r in scoped["items"]] == [ids["beta"]], "scoped users see only their customers"
    assert admin.get("/app/customers").status_code == 200
    print("ui customers smoke ok")


if __name__ == "__main__":
    main()
