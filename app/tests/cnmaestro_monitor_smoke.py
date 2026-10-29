"""MON-01/MON-02 for Cambium: cnMaestro samples in the standard Monitor panels (resources, traffic, radio), the retention keeps the
newest sample of every device, an offline device still shows its last values (Monitor and cnMaestro tab). cnMaestro is mocked."""
import json
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cnmaestro_connector as cnm
from app.cnmaestro_models import CambiumMetricSample
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI266-Cambium-Monitor-Pass"
MAC_ON, MAC_OFF = "02:CA:00:00:02:66", "02:CA:00:00:02:67"


def sample(device_id, at, **extra):
    values = {"cpu_percent": 12.0, "ram_percent": 48.5, "signal_dbm": -61.0, "snr_db": 31.0, "stations": 1,
              "dl_throughput_bps": 4_000_000, "ul_throughput_bps": 600_000, "uptime_seconds": 86_400}
    return CambiumMetricSample(device_id=device_id, observed_at=at, **{**values, **extra})


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="cnmaestro").delete()
        row = ConnectorIntegration(provider="cnmaestro", name="cnMaestro", base_url="https://cnmaestro.example.test", is_enabled=True,
                                   secret_encrypted=encrypt_text(json.dumps({"client_id": "ci", "client_secret": "ci"})), settings={})
        customer = Customer(name=f"CI266 {suffix}", code=f"N{suffix}")
        db.add_all([row, customer, User(username=f"ci266-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.flush()
        online = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_cpe", name=f"CI266-ON-{suffix}", status="online",
                        model="ePMP Force 400", primary_mac=MAC_ON, management_ip="198.51.100.66")
        offline = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_cpe", name=f"CI266-OFF-{suffix}", status="offline",
                         model="ePMP Force 400", primary_mac=MAC_OFF, management_ip="198.51.100.67")
        db.add_all([online, offline])
        db.flush()
        for minutes in (10, 30, 50):
            db.add(sample(online.id, now - timedelta(minutes=minutes)))
        db.add(sample(online.id, now - timedelta(days=100)))  # expired: the device has newer samples
        db.add(sample(offline.id, now - timedelta(days=120), signal_dbm=-70.0))
        db.add(sample(offline.id, now - timedelta(days=95), signal_dbm=-74.0, cpu_percent=7.0))  # the newest one: kept
        db.commit()
        ids = {"on": online.id, "off": offline.id}

    # Retention (MON-02): samples older than 90 days go, except the newest of each device.
    assert cnm.cleanup(now) == 2
    with SessionLocal() as db:
        left = {d: sorted(s.observed_at for s in db.scalars(select(CambiumMetricSample).where(CambiumMetricSample.device_id == d)))
                for d in ids.values()}
    assert len(left[ids["on"]]) == 3 and len(left[ids["off"]]) == 1 and now - left[ids["off"]][0] > timedelta(days=94)
    assert cnm.cleanup(now) == 0, "running again deletes nothing: the last sample of the offline device stays"

    client = TestClient(app, base_url="https://nsm.example.test")
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci266-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303

    # MON-01: cnMaestro history in the Monitor panels every device uses.
    data = client.get(f"/api/ui/v1/devices/{ids['on']}/monitor/source?range=24h").json()
    assert data["source"] == "cnmaestro" and data["sample_count"] == 3, data
    assert data["resources"]["id"] == "resources" and [s["field"] for s in data["resources"]["series"]] == ["cpu_percent", "ram_percent"]
    assert data["traffic"]["id"] == "throughput" and [s["field"] for s in data["traffic"]["series"]] == ["dl_throughput_bps", "ul_throughput_bps"]
    assert [c["id"] for c in data["extra"]] == ["signal", "snr", "stations"]
    assert all(len(s["points"]) == 3 for s in data["traffic"]["series"])
    assert client.get(f"/api/ui/v1/devices/{ids['on']}/monitor/source?range=1h").json()["sample_count"] == 3

    # MON-02: an offline device without samples in the range still shows what it last reported.
    off = client.get(f"/api/ui/v1/devices/{ids['off']}/monitor/source?range=24h").json()
    assert off["sample_count"] == 0 and off["resources"] is None and off["latest"], off
    values = {v["field"]: v["value"] for v in off["latest"]["values"]}
    assert values["signal_dbm"] == -74.0 and values["cpu_percent"] == 7.0

    # A sync where cnMaestro sends no statistics for the device keeps its last values on the cnMaestro tab.
    inventory = [{"mac": mac, "name": name, "status": status, "product": "ePMP Force 400", "software_version": "5.10.4", "type": "epmp"}
                 for mac, name, status in ((MAC_ON, f"CI266-ON-{suffix}", "online"), (MAC_OFF, f"CI266-OFF-{suffix}", "offline"))]
    stats = {"rows": [{"mac": MAC_ON, "cpu": 20, "memory": 50, "rssi": -60}, {"mac": MAC_OFF, "cpu": 9, "memory": 40, "rssi": -72}]}

    def fake(request: httpx.Request):
        if request.url.path.endswith("/access/token"):
            return httpx.Response(200, json={"access_token": "ci-token"})
        rows = inventory if request.url.path == "/api/v2/devices" else stats["rows"] if request.url.path == "/api/v2/devices/statistics" else None
        if rows is None:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": rows, "paging": {"offset": 0, "limit": 100, "total": len(rows)}})

    with SessionLocal() as db:
        assert cnm.sync(db, now=now, transport=httpx.MockTransport(fake))["status"] == "success"
        stats["rows"] = [{"mac": MAC_ON, "cpu": 21, "memory": 51, "rssi": -59}]  # the offline device is no longer in the statistics
        assert cnm.sync(db, now=now + timedelta(minutes=15), transport=httpx.MockTransport(fake))["status"] == "success"
    tab = client.get(f"/api/ui/v1/devices/{ids['off']}/cnmaestro").json()
    shown = {m["field"]: m["value"] for m in tab["metrics"]}
    assert shown["cpu_percent"] == 9 and shown["signal_dbm"] == -72 and tab["meta"]["metrics_at"].startswith(now.isoformat()[:16]), tab
    on_tab = client.get(f"/api/ui/v1/devices/{ids['on']}/cnmaestro").json()
    assert {m["field"]: m["value"] for m in on_tab["metrics"]}["cpu_percent"] == 21

    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="cnmaestro").delete()
        db.commit()
    print("cnmaestro monitor smoke: ok")


if __name__ == "__main__":
    main()
