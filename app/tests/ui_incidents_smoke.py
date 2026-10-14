"""New interface, incident list API: newest first, status counts, filters, devices with vendor icon, root cause, permissions, scope."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.incident_models import Incident, IncidentDevice, IncidentHypothesis
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Incidents-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        mine = Customer(name=f"CI UI Incidents {suffix}", code=f"UI{suffix}")
        other = Customer(name=f"CI UI Incidents other {suffix}", code=f"UJ{suffix}")
        db.add_all([mine, other])
        db.flush()
        devices = [Device(customer_id=mine.id, vendor=vendor, device_type="router", name=f"TEST-UI-{index}-{suffix}", status="online")
                   for index, vendor in enumerate(["mikrotik", "ubiquiti", "mikrotik", "mikrotik", "generic"])]
        db.add_all(devices)
        db.flush()

        def incident(title, status, hours, customer=mine, linked=(), resolved=None):
            row = Incident(customer_id=customer.id, title=f"TEST {title} {suffix}", summary=f"TEST summary {title}", severity="high", status=status,
                           started_at=now - timedelta(hours=hours), resolved_at=resolved)
            db.add(row)
            db.flush()
            for device in linked:
                db.add(IncidentDevice(incident_id=row.id, device_id=device.id))
            return row

        outage = incident("fibre outage", "investigating", 1, linked=devices)
        flapping = incident("radio flapping", "open", 3, linked=devices[1:2])
        watched = incident("watched link", "monitoring", 5)
        closed = incident("closed power", "resolved", 30, resolved=now - timedelta(hours=28))
        foreign = incident("foreign", "open", 2, customer=other)
        cause = IncidentHypothesis(incident_id=closed.id, statement="TEST power supply failed", category="power", origin="operator", status="confirmed")
        db.add(cause)
        db.flush()
        closed.root_cause_hypothesis_id = cause.id
        db.add_all([
            User(username=f"ci-ui-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-ui-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-ui-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {k: str(v.id) for k, v in dict(mine=mine, outage=outage, flapping=flapping, watched=watched, closed=closed, foreign=foreign).items()}
        device_ids = [str(device.id) for device in devices]

    client = login(f"ci-ui-{suffix}")
    page = client.get(f"/api/ui/v1/incidents?customer={ids['mine']}").json()
    assert [row["id"] for row in page["items"]] == [ids["outage"], ids["flapping"], ids["watched"]], "active incidents, newest first"
    assert page["counts"] == {"active": 3, "open": 1, "investigating": 1, "monitoring": 1, "resolved": 1, "all": 4}, page["counts"]
    assert page["total"] == 3 and page["can_write"] is True
    outage_row = page["items"][0]
    assert outage_row["device_count"] == 5 and len(outage_row["devices"]) == 3, "three devices shown, the rest counted"
    assert all(device["brand"]["symbol"].startswith("brand-") for device in outage_row["devices"])
    assert outage_row["customer"] == {"id": ids["mine"], "name": f"CI UI Incidents {suffix}"}
    assert page["items"][1]["devices"][0] == {**page["items"][1]["devices"][0], "id": device_ids[1]}
    assert page["items"][1]["devices"][0]["brand"]["symbol"] == "brand-ubiquiti"

    resolved = client.get(f"/api/ui/v1/incidents?customer={ids['mine']}&status=resolved").json()["items"]
    assert [row["id"] for row in resolved] == [ids["closed"]]
    assert resolved[0]["root_cause"] == {"statement": "TEST power supply failed", "category": "power"} and resolved[0]["resolved_at"]
    every = client.get(f"/api/ui/v1/incidents?customer={ids['mine']}&status=all").json()
    assert every["total"] == 4
    found = client.get(f"/api/ui/v1/incidents?status=all&q=flapping {suffix}").json()
    assert [row["id"] for row in found["items"]] == [ids["flapping"]] and found["counts"]["all"] == 1, "search narrows the counts too"
    assert client.get("/app/incidents?status=all").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    auditor = login(f"ci-ui-au-{suffix}")
    seen = auditor.get(f"/api/ui/v1/incidents?customer={ids['mine']}").json()
    assert seen["total"] == 3 and seen["can_write"] is False

    scoped = login(f"ci-ui-scoped-{suffix}")
    rows = {row["id"] for row in scoped.get("/api/ui/v1/incidents?status=all").json()["items"]}
    assert ids["foreign"] in rows and not rows & {ids["outage"], ids["closed"]}, "other customers stay hidden"
    print("ui incidents smoke ok")


if __name__ == "__main__":
    main()
