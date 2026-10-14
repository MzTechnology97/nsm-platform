"""New interface, device list API: filters, sorting, blocks, counts, facets and customer scope."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, Site, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Devices-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        alpha = Customer(name=f"CI Alpha {suffix}", code=f"UA{suffix[:6]}")
        beta = Customer(name=f"CI Beta {suffix}", code=f"UB{suffix[:6]}")
        db.add_all([alpha, beta])
        db.flush()
        north = Site(customer_id=alpha.id, name=f"Nord {suffix}")
        db.add(north)
        db.flush()
        for index in range(130):
            db.add(Device(customer_id=alpha.id, site_id=north.id if index % 2 else None, vendor="mikrotik" if index % 3 else "ubiquiti",
                          device_type="router", name=f"TEST-UD-{index:03d}-{suffix}", status="online" if index % 4 else "offline",
                          management_ip=f"198.51.100.{index % 250}", model="CCR2004" if index == 7 else "hAP ax2", last_seen=now - timedelta(minutes=index)))
        db.add(Device(customer_id=beta.id, vendor="cambium", device_type="wireless_cpe", name=f"TEST-UD-BETA-{suffix}", status="pending_enrollment"))
        db.add_all([
            User(username=f"ci-ud-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-ud-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(beta.id)]),
        ])
        db.commit()
        ids = {"alpha": str(alpha.id), "beta": str(beta.id), "north": str(north.id)}

    assert TestClient(app).get("/api/ui/v1/devices").status_code == 401
    client = login(f"ci-ud-{suffix}")
    base = f"/api/ui/v1/devices?customer={ids['alpha']}"

    first = client.get(base + "&limit=100").json()
    assert first["total"] == 130 and len(first["items"]) == 100 and first["offset"] == 0
    assert first["counts"]["all"] == 130 and first["counts"]["offline"] == 33 and first["counts"]["online"] == 97
    assert first["items"][0]["name"] == f"TEST-UD-000-{suffix}", "sorted by name"
    second = client.get(base + "&offset=100&limit=100").json()
    assert len(second["items"]) == 30 and {i["id"] for i in second["items"]}.isdisjoint({i["id"] for i in first["items"]})

    row = client.get(base + "&q=CCR2004").json()["items"]
    assert len(row) == 1 and row[0]["model"] == "CCR2004" and row[0]["customer"]["name"] == f"CI Alpha {suffix}"
    assert row[0]["site"]["name"] == f"Nord {suffix}" and row[0]["management_ip"] == "198.51.100.7"
    assert client.get(base + "&q=198.51.100.12").json()["total"] >= 1
    offline = client.get(base + "&status=offline").json()
    assert offline["total"] == 33 and all(i["status"] == "offline" for i in offline["items"])
    assert offline["counts"]["all"] == 130, "counts follow the customer scope, not the status filter"
    assert client.get(base + f"&site={ids['north']}").json()["total"] == 65
    assert client.get(base + "&vendor=ubiquiti").json()["total"] == 44
    recent = client.get(base + "&sort=last_seen").json()["items"]
    assert recent[0]["name"] == f"TEST-UD-000-{suffix}" and recent[1]["name"] == f"TEST-UD-001-{suffix}"
    reverse = client.get(base + "&sort=name&dir=desc").json()["items"]
    assert reverse[0]["name"] == f"TEST-UD-129-{suffix}"
    assert client.get(f"/api/ui/v1/devices?status=pending&customer={ids['beta']}").json()["total"] == 1
    assert client.get(base + "&sort=evil").status_code == 422
    assert client.get(base + "&limit=1000").status_code == 422

    facets = client.get("/api/ui/v1/devices/facets").json()
    assert {"mikrotik", "ubiquiti", "cambium"} <= set(facets["vendors"])
    assert any(c["id"] == ids["alpha"] for c in facets["customers"]) and any(s["id"] == ids["north"] for s in facets["sites"])

    # A user limited to one customer never sees the others.
    scoped = login(f"ci-ud-scoped-{suffix}")
    mine = scoped.get("/api/ui/v1/devices").json()
    assert mine["total"] == 1 and mine["items"][0]["customer"]["id"] == ids["beta"]
    assert scoped.get(f"/api/ui/v1/devices?customer={ids['alpha']}").json()["total"] == 0
    assert all(c["id"] != ids["alpha"] for c in scoped.get("/api/ui/v1/devices/facets").json()["customers"])
    print("UI devices smoke passed")


if __name__ == "__main__":
    main()
