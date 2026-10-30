"""New interface, exposed services API: per-device state (exposed, clean, to set up), findings, views, customer filter and scope."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Exposure-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    # These checks predate SCAN-02: every test device counts as reachable from Internet (reachability_smoke covers NAT).
    from app import reachability
    reachability.current = lambda device: {"state": "public", "reason": "test", "public_addresses": []}
    suffix = uuid.uuid4().hex[:6]
    now = utcnow().isoformat()
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Exposure {suffix}", code=f"UE{suffix}")
        other = Customer(name=f"CI UI Exposure other {suffix}", code=f"UO{suffix}")
        db.add_all([customer, other])
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-UE-RTR-{suffix}", status="online",
                        inventory_data={"agent_transport": "modern", "agent_version": "0.49.22", "exposure": {
                            "checked_at": now, "source": "mikrotik_agent", "exposed": 1, "worst": "high",
                            "findings": [{"service": "winbox", "label": "winbox", "port": 8291, "proto": "tcp", "state": "exposed", "severity": "high"},
                                         {"service": "ssh", "label": "ssh", "port": 22, "proto": "tcp", "state": "protected", "severity": "info"}],
                            "port_forwards": [{"protocol": "tcp", "public_ports": "8080", "to_address": "192.0.2.20", "sensitive": ["HTTP"], "certain": True, "severity": "high"}]}})
        clean = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UE-UBNT-{suffix}", status="online",
                       inventory_data={"exposure": {"checked_at": now, "source": "external", "target_ip": "198.51.100.7", "exposed": 0, "findings": []}})
        setup = Device(customer_id=customer.id, vendor="tp-link", device_type="cpe", name=f"TEST-UE-TPL-{suffix}", management_ip="192.0.2.30", status="online")
        foreign = Device(customer_id=other.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UE-FOREIGN-{suffix}", status="online",
                         inventory_data={"exposure": {"checked_at": now, "source": "external", "target_ip": "198.51.100.8", "exposed": 1,
                                                      "findings": [{"label": "telnet", "port": 23, "proto": "tcp", "state": "exposed", "severity": "critical"}]}})
        db.add_all([router, clean, setup, foreign])
        db.add_all([
            User(username=f"ci-ue-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-ue-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {k: str(v.id) for k, v in dict(router=router, clean=clean, setup=setup, foreign=foreign, customer=customer).items()}

    base = f"/api/ui/v1/exposure?customer={ids['customer']}"
    assert TestClient(app).get(base).status_code == 401
    client = login(f"ci-ue-{suffix}")
    body = client.get(base).json()
    assert body["view"] == "exposed" and [r["id"] for r in body["items"]] == [ids["router"]]
    row = body["items"][0]
    assert row["mode"] == "agent" and row["worst"] == "high" and len(row["findings"]) == 2, row
    assert {f["label"] for f in row["findings"]} >= {"winbox"} and any(f["port"] == 8291 for f in row["findings"])
    assert body["counts"] == {"exposed": 1, "internal": 0, "clean": 1, "setup": 1, "pending": 0, "critical": 0, "total": 3}, body["counts"]
    setup = client.get(f"{base}&view=setup").json()["items"]
    assert [r["id"] for r in setup] == [ids["setup"]] and setup[0]["blocker"]
    clean = client.get(f"{base}&view=clean").json()["items"]
    assert [r["id"] for r in clean] == [ids["clean"]] and clean[0]["mode"] == "external" and clean[0]["target_ip"] == "198.51.100.7"
    assert client.get(f"{base}&view=all").json()["total"] == 3
    # Search and service filter: the service list counts what the search leaves, before the service is applied.
    assert {s["label"]: s["count"] for s in body["services"]}["winbox"] == 1
    other = next(f["label"] for f in row["findings"] if f["label"] != "winbox")
    assert [r["id"] for r in client.get(f"{base}&service=winbox").json()["items"]] == [ids["router"]]
    unknown = client.get(f"{base}&service=gopher").json()
    assert unknown["items"] == [] and {s["label"] for s in unknown["services"]} >= {"winbox", other}
    assert [r["id"] for r in client.get(f"{base}&view=all&q=198.51.100.7").json()["items"]] == [ids["clean"]]
    assert client.get(f"{base}&q=nothing-like-this").json()["total"] == 0
    assert client.get(f"{base}&view=bogus").json()["view"] == "exposed"

    scoped = login(f"ci-ue-scoped-{suffix}").get("/api/ui/v1/exposure?view=all").json()
    assert [r["id"] for r in scoped["items"]] == [ids["foreign"]] and scoped["counts"]["critical"] == 1, "scoped users see only their devices"
    assert client.get("/app/exposure?view=setup").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)
    print("ui exposure smoke ok")


if __name__ == "__main__":
    main()
