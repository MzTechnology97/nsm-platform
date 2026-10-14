"""New interface, MikroTik Agent fleet API: health and installation states, update policy, filters, summary and scope."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_agent_generation import TARGET_AGENT_VERSION as CURRENT_AGENT
from app.models import Customer, Device, DeviceEnrollment, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Agents-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Agents {suffix}", code=f"UA{suffix[:6]}")
        other = Customer(name=f"CI UI Agents other {suffix}", code=f"UB{suffix[:6]}")
        admin = User(username=f"ci-ua-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        db.add_all([customer, other, admin])
        db.flush()

        def device(label, status="online", last_seen=now, inventory=None, owner=customer):
            row = Device(customer_id=owner.id, vendor="mikrotik", device_type="router", name=f"TEST-UA-{label}-{suffix}", model="CCR2004",
                         firmware_version="7.20.7 (stable)", status=status, last_seen=last_seen, management_source="mikrotik_agent",
                         management_ip="198.51.100.90", inventory_data=inventory or {})
            db.add(row)
            db.flush()
            return row

        healthy = device("OK", inventory={"agent_version": CURRENT_AGENT, "agent_transport": "modern", "agent_privilege_profile": "ops-v2", "last_source_ip": "198.51.100.35"})
        outdated = device("OLD", inventory={"agent_version": "0.49.12", "agent_transport": "modern"})
        offline = device("OFF", status="offline", inventory={"agent_version": CURRENT_AGENT, "agent_transport": "modern"})
        pending = device("NEW", status="pending_enrollment", last_seen=None)
        foreign = device("FOREIGN", inventory={"agent_version": CURRENT_AGENT, "agent_transport": "modern"}, owner=other)
        db.add(Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UA-CPE-{suffix}", status="online"))
        for index, item in enumerate((healthy, outdated, offline, foreign), start=1):
            db.add(DeviceAgentCredential(device_id=item.id, agent_type="mikrotik_agent", secret_hash=(str(index) * 64)[:64], is_active=True, last_used_at=now))
        db.add(DeviceEnrollment(device_id=pending.id, source="mikrotik_bootstrap", token_hash=uuid.uuid4().hex * 2, status="pending",
                                expires_at=now + timedelta(minutes=30), created_by_user_id=admin.id))
        db.add_all([
            User(username=f"ci-ua-op-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True),
            User(username=f"ci-ua-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {k: str(v.id) for k, v in dict(healthy=healthy, outdated=outdated, offline=offline, pending=pending, foreign=foreign, customer=customer).items()}

    base = f"/api/ui/v1/agents?customer={ids['customer']}"
    assert TestClient(app).get(base).status_code == 401
    client = login(f"ci-ua-{suffix}")
    body = client.get(f"{base}&state=all").json()
    rows = {row["id"]: row for row in body["items"]}
    assert set(rows) == {ids["healthy"], ids["outdated"], ids["offline"], ids["pending"]}, "MikroTik only, this customer only"
    assert rows[ids["healthy"]]["health"] == "healthy" and rows[ids["healthy"]]["install"] == "verified" and rows[ids["healthy"]]["source_ip"] == "198.51.100.35"
    old = rows[ids["outdated"]]
    assert old["health"] == "agent_outdated" and old["update"]["outdated"] and old["update"]["target_version"] == CURRENT_AGENT and old["attention"]
    assert rows[ids["offline"]]["health"] == "offline"
    new = rows[ids["pending"]]
    assert new["install"] == "waiting" and new["pending_enrollments"] == 1 and not new["credential_active"]
    summary = body["summary"]
    assert (summary["total"], summary["healthy"], summary["attention"], summary["outdated"], summary["offline"], summary["modern"]) == (4, 1, 3, 2, 1, 3), summary  # OFF lacks the ops-v2 profile

    attention = client.get(base).json()
    assert attention["state"] == "attention" and ids["healthy"] not in {r["id"] for r in attention["items"]}
    assert [r["id"] for r in client.get(f"{base}&state=offline").json()["items"]] == [ids["offline"]]
    assert [r["id"] for r in client.get(f"{base}&state=all&install=waiting").json()["items"]] == [ids["pending"]]
    assert {r["id"] for r in client.get(f"{base}&state=all&transport=modern").json()["items"]} == {ids["healthy"], ids["outdated"], ids["offline"]}
    assert [r["id"] for r in client.get(f"/api/ui/v1/agents?state=all&q=TEST-UA-OLD-{suffix}").json()["items"]] == [ids["outdated"]]
    assert client.get(f"{base}&state=bogus&transport=bogus").json()["state"] == "attention"
    assert login(f"ci-ua-op-{suffix}").get(base).status_code == 200, "operators read the fleet"

    scoped = login(f"ci-ua-scoped-{suffix}").get("/api/ui/v1/agents?state=all").json()
    assert [r["id"] for r in scoped["items"]] == [ids["foreign"]], "scoped users see only their customers' agents"
    assert client.get("/app/agents?state=offline").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)
    print("ui agents smoke ok")


if __name__ == "__main__":
    main()
