"""New interface, global search and notifications: matches with vendor icon, customer scope, read state per user, CSRF, classic redirects."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.main import norm_mac
from app.models import Customer, Device, Notification, Site, User
from app.security import hash_password

PASSWORD = "CI-UI-Personal-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    anonymous = TestClient(app)
    for path in ("/api/ui/v1/search?q=x", "/api/ui/v1/notifications", "/api/ui/v1/notifications/summary"):
        assert anonymous.get(path).status_code == 401, path

    suffix = uuid.uuid4().hex[:6]
    mac = norm_mac(f"02:00:5e:{suffix[0:2]}:{suffix[2:4]}:{suffix[4:6]}")
    with SessionLocal() as db:
        mine = Customer(name=f"CI UI Personal {suffix}", code=f"UP{suffix}")
        other = Customer(name=f"CI UI Personal other {suffix}", code=f"UQ{suffix}")
        db.add_all([mine, other])
        db.flush()
        site = Site(customer_id=mine.id, name=f"CI site {suffix}", address=f"Via Esempio {suffix}")
        db.add(site)
        db.flush()
        router = Device(customer_id=mine.id, site_id=site.id, vendor="mikrotik", device_type="router", name=f"TEST-UP-MT-{suffix}",
                        display_name=f"TEST-UP-MT-{suffix}", status="online", model="hAP ax2", serial_number=f"SN{suffix}",
                        primary_mac=mac, management_ip="192.0.2.41")
        hidden = Device(customer_id=other.id, vendor="ubiquiti", device_type="radio", name=f"TEST-UP-UB-{suffix}",
                        display_name=f"TEST-UP-UB-{suffix}", status="offline")
        db.add_all([router, hidden])
        db.flush()
        db.add_all([
            User(username=f"ci-up-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True),
            User(username=f"ci-up-b-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-up-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True,
                 customer_scope=[str(mine.id)]),
        ])
        first = Notification(severity="critical", category="security", title=f"CI notice A {suffix}", message="Synthetic notice",
                             device_id=router.id, source_url="https://example.test/advisory")
        second = Notification(severity="info", category="system", title=f"CI notice B {suffix}", source_url="javascript:alert(1)")
        inactive = Notification(severity="info", category="system", title=f"CI notice inactive {suffix}", is_active=False)
        db.add_all([first, second, inactive])
        db.commit()
        ids = {"router": str(router.id), "site": str(site.id), "mine": str(mine.id), "first": str(first.id), "second": str(second.id)}

    client, csrf = login(f"ci-up-{suffix}")

    # Search: name, serial, MAC (any notation) and IP find the device, with its vendor icon, customer and site.
    for term in (f"TEST-UP-MT-{suffix}", f"SN{suffix}", mac.replace(":", "-").lower(), "192.0.2.41"):
        data = client.get("/api/ui/v1/search", params={"q": term}).json()
        found = [d for d in data["devices"] if d["id"] == ids["router"]]
        assert found, term
    device = found[0]
    assert device["brand"]["symbol"] == "brand-mikrotik" and device["customer"]["id"] == ids["mine"] and device["site"]["id"] == ids["site"]
    data = client.get("/api/ui/v1/search", params={"q": f"Personal {suffix}"}).json()
    assert {c["id"] for c in data["customers"]} >= {ids["mine"]}
    data = client.get("/api/ui/v1/search", params={"q": f"Via Esempio {suffix}"}).json()
    assert [s["id"] for s in data["sites"]] == [ids["site"]] and data["sites"][0]["customer"]["id"] == ids["mine"]
    empty = client.get("/api/ui/v1/search", params={"q": "   "}).json()
    assert empty == {"q": "", "customers": [], "sites": [], "devices": []}

    scoped, _ = login(f"ci-up-scoped-{suffix}")
    data = scoped.get("/api/ui/v1/search", params={"q": "TEST-UP-"}).json()
    names = {d["name"] for d in data["devices"]}
    assert f"TEST-UP-MT-{suffix}" in names and f"TEST-UP-UB-{suffix}" not in names, "customer scope"

    # Notifications: active only, newest first, device with icon, unsafe link dropped.
    listing = client.get("/api/ui/v1/notifications").json()
    mine_rows = {row["id"]: row for row in listing["items"] if suffix in row["title"]}
    assert set(mine_rows) == {ids["first"], ids["second"]}, "inactive notifications are hidden"
    assert mine_rows[ids["first"]]["device"]["brand"]["symbol"] == "brand-mikrotik"
    assert mine_rows[ids["first"]]["source_url"] == "https://example.test/advisory" and mine_rows[ids["second"]]["source_url"] is None
    assert not mine_rows[ids["first"]]["is_read"]
    unread = client.get("/api/ui/v1/notifications/summary").json()["unread"]
    assert unread == listing["unread"] and unread >= 2

    assert client.post(f"/api/ui/v1/notifications/{ids['first']}/read").status_code == 403, "CSRF header required"
    response = client.post(f"/api/ui/v1/notifications/{ids['first']}/read", headers=csrf)
    assert response.status_code == 200 and response.json()["unread"] == unread - 1
    assert client.post(f"/api/ui/v1/notifications/{ids['first']}/read", headers=csrf).json()["unread"] == unread - 1, "idempotent"
    assert client.post(f"/api/ui/v1/notifications/{uuid.uuid4()}/read", headers=csrf).status_code == 404
    assert client.post("/api/ui/v1/notifications/not-a-uuid/read", headers=csrf).status_code == 404

    # Read state is personal.
    other_client, other_csrf = login(f"ci-up-b-{suffix}")
    rows = {row["id"]: row for row in other_client.get("/api/ui/v1/notifications").json()["items"]}
    assert not rows[ids["first"]]["is_read"]

    marked = client.post("/api/ui/v1/notifications/read-all", headers=csrf).json()
    assert marked["unread"] == 0 and marked["marked"] >= 1
    assert all(row["is_read"] for row in client.get("/api/ui/v1/notifications").json()["items"])
    assert other_client.get("/api/ui/v1/notifications/summary").json()["unread"] >= 2

    # The classic pages now point to the new interface.
    for old, new in (("/notifications", "/app/notifications"), (f"/search?q=SN{suffix}", f"/app/search?q=SN{suffix}")):
        moved = other_client.get(old, follow_redirects=False)
        assert moved.status_code == 302 and moved.headers["location"] == new, (old, moved.headers.get("location"))

    for path in ("/app/search?q=x", "/app/notifications"):
        assert client.get(path).status_code in (200, 503), path
    print("ui personal smoke ok")


if __name__ == "__main__":
    main()
