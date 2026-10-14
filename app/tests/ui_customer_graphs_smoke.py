"""New interface, customer Graphs tab: summary API (sites compared, top traffic, 30-day availability), site scope, permissions."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.agent_models import DeviceInterfaceSample, DevicePingSample
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, Site, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Customer-Graphs-2026"
WAN = {"pppoe-out1": {"type": "pppoe-out"}, "ether2": {"type": "ether"}}


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Graphs {suffix}", code=f"UG{suffix}")
        other = Customer(name=f"CI UI Graphs other {suffix}", code=f"UO{suffix}")
        db.add_all([customer, other])
        db.flush()
        north, south = Site(customer_id=customer.id, name=f"Nord {suffix}"), Site(customer_id=customer.id, name=f"Sud {suffix}")
        foreign = Site(customer_id=other.id, name=f"Estranea {suffix}")
        db.add_all([north, south, foreign])
        db.flush()
        r1 = Device(customer_id=customer.id, site_id=north.id, vendor="mikrotik", device_type="router", name=f"TEST-UG-R1-{suffix}", status="online",
                    inventory_data={"interface_counters": WAN})
        r2 = Device(customer_id=customer.id, site_id=south.id, vendor="mikrotik", device_type="router", name=f"TEST-UG-R2-{suffix}", status="offline",
                    inventory_data={"interface_counters": WAN})
        loose = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UG-U-{suffix}", status="online")
        db.add_all([r1, r2, loose])
        db.flush()
        for minutes in range(5, 120, 5):
            at = now - timedelta(minutes=minutes)
            db.add(DeviceInterfaceSample(device_id=r1.id, interface="pppoe-out1", observed_at=at, rx_bps=40e6, tx_bps=5e6))
            db.add(DeviceInterfaceSample(device_id=r2.id, interface="pppoe-out1", observed_at=at, rx_bps=10e6, tx_bps=2e6))
            db.add(DevicePingSample(device_id=r1.id, observed_at=at, target="198.51.100.9", sent=3, received=3 if minutes != 30 else 0,
                                    rtt_min=8.0, rtt_avg=10.0, rtt_max=25.0))
        db.add_all([
            User(username=f"ci-ug-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-ug-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {"customer": str(customer.id), "north": str(north.id), "south": str(south.id), "foreign": str(foreign.id), "r1": str(r1.id), "r2": str(r2.id)}

    base = f"/api/ui/v1/customers/{ids['customer']}/graphs/summary"
    assert TestClient(app).get(base).status_code == 401
    client = login(f"ci-ug-{suffix}")
    body = client.get(base).json()
    assert body["site"] is None and body["devices"] == 3 and [s["name"] for s in body["sites"]] == [f"Nord {suffix}", f"Sud {suffix}"]
    overview = {(row["site"] or {}).get("id"): row for row in body["overview"]}
    assert set(overview) == {ids["north"], ids["south"], None}, "sites plus the devices without a site"
    assert overview[ids["north"]]["rx_bps"] == 40e6 and overview[ids["south"]]["online"] == 0 and overview[None]["devices"] == 1
    assert [row["id"] for row in body["top"]] == [ids["r1"], ids["r2"]] and body["top"][0]["rx_bps"] == 40e6
    avail = body["availability"]
    assert avail["measured"] == 1 and avail["devices"][0]["device_id"] == ids["r1"] and avail["devices"][0]["availability"] < 100
    assert body["ranges"] == ["24h", "7d", "30d", "90d"]

    site = client.get(f"{base}?site={ids['north']}").json()
    assert site["site"]["id"] == ids["north"] and site["devices"] == 1 and site["overview"] == []
    assert [row["id"] for row in site["top"]] == [ids["r1"]]
    assert client.get(f"{base}?site={ids['foreign']}").json()["site"] is None, "another customer's site is ignored"
    charts = client.get(f"/api/v1/customers/{ids['customer']}/graphs?range=7d&site={ids['south']}").json()
    assert {c["id"] for c in charts["charts"]} >= {"traffic"} and charts["devices"] == 1

    scoped = login(f"ci-ug-scoped-{suffix}")
    assert scoped.get(base).status_code == 404, "another customer's graphs are invisible"
    assert client.get(f"/api/ui/v1/customers/{uuid.uuid4()}/graphs/summary").status_code == 404
    assert client.get(f"/app/customers/{ids['customer']}/graphs?range=7d").status_code == 200
    print("ui customer graphs smoke ok")


if __name__ == "__main__":
    main()
