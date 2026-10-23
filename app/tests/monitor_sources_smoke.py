"""Monitor tab for every device: TR-069 charts in the standard panels (resources, traffic, the rest), 1h range,
no source for MikroTik and devices without one, the Monitor tab listed for every vendor."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.acs_models import AcsMetricSample
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI242-Monitor-Sources-Pass"


def login(username):
    client = TestClient(app, base_url="https://nsm.example.test")
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI242 {suffix}", code=f"M{suffix}")
        db.add_all([customer, User(username=f"ci242-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"CI242-CPE-{suffix}", status="online",
                     management_source="tr069", external_device_id=f"02AABB-HB210-CI242{suffix}", management_ip="198.51.100.42")
        mt = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"CI242-MT-{suffix}", status="online")
        generic = Device(customer_id=customer.id, vendor="generic", device_type="switch", name=f"CI242-SW-{suffix}", status="unknown",
                         management_source="manual")
        db.add_all([cpe, mt, generic])
        db.flush()
        for minutes in (50, 30, 10, 60 * 5):
            db.add(AcsMetricSample(device_id=cpe.id, observed_at=now - timedelta(minutes=minutes), uptime_seconds=1000, cpu_percent=5.0,
                                   ram_percent=67.5, wan_rx_bps=2_000_000, wan_tx_bps=300_000, wifi_clients=3, lan_hosts=2))
        db.commit()
        ids = {"cpe": cpe.id, "mt": mt.id, "generic": generic.id}

    client = login(f"ci242-{suffix}")
    data = client.get(f"/api/ui/v1/devices/{ids['cpe']}/monitor/source?range=24h").json()
    assert data["source"] == "tr069" and data["sample_count"] == 4
    assert data["resources"]["id"] == "resources" and [s["field"] for s in data["resources"]["series"]] == ["cpu_percent", "ram_percent"]
    assert data["traffic"]["id"] == "acs_wan" and [s["field"] for s in data["traffic"]["series"]] == ["wan_rx_bps", "wan_tx_bps"]
    assert [c["id"] for c in data["extra"]] == ["acs_clients"], "DSL charts only when the CPE reports a DSL line"
    hour = client.get(f"/api/ui/v1/devices/{ids['cpe']}/monitor/source?range=1h").json()
    assert hour["sample_count"] == 3, "the Monitor 1h range is accepted by the sources too"
    assert client.get(f"/api/ui/v1/devices/{ids['cpe']}/monitor/source?range=2y").status_code == 422

    for key in ("mt", "generic"):
        none = client.get(f"/api/ui/v1/devices/{ids[key]}/monitor/source").json()
        assert none["source"] is None and none["resources"] is None and none["extra"] == []
    assert client.get(f"/api/ui/v1/devices/{uuid.uuid4()}/monitor/source").status_code == 404

    for key in ids:
        tabs = [tab["key"] for tab in client.get(f"/api/ui/v1/devices/{ids[key]}").json()["tabs"]]
        assert "monitor" in tabs, (key, tabs)
    print("monitor sources smoke: ok")


if __name__ == "__main__":
    main()
