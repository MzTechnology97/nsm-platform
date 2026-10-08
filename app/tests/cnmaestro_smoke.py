"""VEND-02: cnMaestro connector — OAuth2, paged inventory and statistics, linking by MAC/serial, metrics history and pages."""
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import cnmaestro_connector as cnm
from app.cnmaestro_models import CambiumMetricSample
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-cnMaestro-2026"


class FakeCnMaestro:
    def __init__(self, devices, stats):
        self.devices, self.stats, self.calls = devices, stats, []

    def __call__(self, request: httpx.Request):
        path = request.url.path
        self.calls.append(path)
        if path == "/api/v1/access/token":
            assert request.method == "POST" and b"grant_type=client_credentials" in request.content
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "ci-token", "expires_in": 3600})
        assert request.headers.get("authorization") == "Bearer ci-token"
        offset, limit = int(request.url.params.get("offset", 0)), int(request.url.params.get("limit", 100))
        rows = self.devices if path == "/api/v1/devices" else self.stats if path == "/api/v1/devices/statistics" else None
        if rows is None:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": rows[offset:offset + limit], "paging": {"total": len(rows), "limit": limit, "offset": offset}})


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    assert cnm.normalize_base_url("https://cnmaestro.example.test/api/v1/") == "https://cnmaestro.example.test"
    for bad in ("ftp://x", "https://user:pw@cnmaestro.example.test", "https://localhost"):
        try:
            cnm.normalize_base_url(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    parsed = cnm.metrics({"cpu": 12, "memory": 40.5, "radio": {"dl_rssi": -61, "dl_snr": 30, "dl_throughput": 52000000}, "connected_sms": 14, "uptime": 86400})
    assert parsed == {"signal_dbm": -61.0, "snr_db": 30.0, "cpu_percent": 12.0, "ram_percent": 40.5, "stations": 14, "uptime_seconds": 86400,
                      "dl_throughput_bps": 52000000}, parsed
    assert cnm.metrics({"cpu": 250, "rssi": 5}) == {}, "implausible values are dropped"

    suffix = uuid.uuid4().hex[:6]
    filler = [{"mac": f"02:CB:00:01:{i // 256:02X}:{i % 256:02X}", "name": f"filler-{i}", "status": "online"} for i in range(150)]
    devices = filler + [
        {"mac": "02:CB:00:00:00:01", "serial_number": "CB-AP-1", "name": "AP-Tower-1", "ip": "198.51.100.71", "status": "online", "product": "ePMP 3000",
         "software_version": "4.7.0.1", "type": "epmp", "network": "Rete Nord", "tower": "Torre 1"},
        {"mac": "02:CB:00:00:00:99", "serial_number": "CB-SM-9", "name": "SM-Rossi", "ip": "198.51.100.72", "status": "offline", "product": "Force 300-25",
         "software_version": "4.6.2", "type": "epmp"},
    ]
    stats = [{"mac": "02:CB:00:00:00:01", "cpu": 23, "memory": 51, "connected_sms": 17, "uptime": 360000, "radio": {"dl_throughput": 88000000, "ul_throughput": 21000000}},
             {"mac": "02:CB:00:00:00:99", "radio": {"dl_rssi": -64, "dl_snr": 27}}]
    fake = FakeCnMaestro(devices, stats)
    transport = httpx.MockTransport(fake)
    original = cnm.client_for
    cnm.client_for = lambda row, transport_=None: original(row, transport)

    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "cnmaestro"))
        customer = Customer(name=f"CI cnMaestro {suffix}", code=f"CN{suffix}")
        db.add(customer)
        db.flush()
        ap = Device(customer_id=customer.id, vendor="generic", device_type="wireless_ap", name=f"TEST-CN-AP-{suffix}", primary_mac="02:CB:00:00:00:01",
                    status="unknown", inventory_data={"manufacturer": "cambium"})
        sm = Device(customer_id=customer.id, vendor="generic", device_type="wireless_cpe", name=f"TEST-CN-SM-{suffix}", serial_number="CB-SM-9",
                    status="unknown", inventory_data={"manufacturer": "cambium"})
        lost = Device(customer_id=customer.id, vendor="generic", device_type="wireless_cpe", name=f"TEST-CN-X-{suffix}", primary_mac="02:CB:00:00:00:77",
                      inventory_data={"manufacturer": "cambium"}, status="unknown")
        other = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-CN-U-{suffix}", primary_mac="02:CB:00:00:00:01", status="online")
        db.add_all([ap, sm, lost, other])
        db.add(User(username=f"ci-cn-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {"ap": ap.id, "sm": sm.id, "lost": lost.id, "other": other.id}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-cn-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get("/admin/integrations/cnmaestro").text
    saved = client.post("/admin/integrations/cnmaestro", data={"csrf": csrf_from(page), "base_url": "https://cnmaestro.example.test", "client_id": "ci-client",
                                                               "client_secret": "ci-secret-value", "verify_tls": "1", "is_enabled": "1"})
    assert "Configurazione cnMaestro salvata" in saved.text and "ci-secret-value" not in saved.text
    with SessionLocal() as db:
        assert "ci-secret-value" not in cnm.connection_row(db).secret_encrypted
    tested = client.post("/admin/integrations/cnmaestro/test", data={"csrf": csrf_from(saved.text)})
    assert "Connessione riuscita" in tested.text
    synced = client.post("/admin/integrations/cnmaestro/sync", data={"csrf": csrf_from(tested.text)})
    assert "Sincronizzazione completata" in synced.text and "152 apparati in cnMaestro" in synced.text, synced.text[-2000:]
    assert fake.calls.count("/api/v1/devices") >= 3, "inventory read in pages (test call + two pages)"

    with SessionLocal() as db:
        ap, sm, lost, other = (db.get(Device, ids[k]) for k in ("ap", "sm", "lost", "other"))
        assert ap.inventory_data["cnmaestro"]["mac"] == "02:CB:00:00:00:01" and ap.management_ip == "198.51.100.71" and ap.firmware_version == "4.7.0.1"
        assert ap.model == "ePMP 3000" and ap.status == "online" and ap.inventory_data["cnmaestro"]["tower"] == "Torre 1"
        assert sm.inventory_data["cnmaestro"]["mac"] == "02:CB:00:00:00:99" and sm.status == "offline", "linked by serial"
        assert "cnmaestro" not in (lost.inventory_data or {}) and "cnmaestro" not in (other.inventory_data or {}), "only Cambium devices with a match"
        samples = {s.device_id: s for s in db.scalars(select(CambiumMetricSample).where(CambiumMetricSample.device_id.in_([ap.id, sm.id])))}
        assert samples[ap.id].stations == 17 and samples[ap.id].dl_throughput_bps == 88000000 and samples[sm.id].signal_dbm == -64.0
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "CNMAESTRO_DEVICE_LINKED", AuditEvent.device_id == ap.id)) is not None
        assert cnm.sync(db, now=utcnow() + timedelta(minutes=1), transport=transport)["samples"] == 0, "at most one sample every few minutes"

    device_page = client.get(f"/devices/{ids['sm']}/cnmaestro").text
    assert "Collegato a cnMaestro" in device_page and "-64 dBm" in device_page and "Grafici cnMaestro" in device_page and "rrd_chart.js" in device_page
    assert f'/devices/{ids["ap"]}/cnmaestro' in client.get(f"/devices/{ids['ap']}").text, "cnMaestro tab on Cambium devices"
    assert f'/devices/{ids["other"]}/cnmaestro' not in client.get(f"/devices/{ids['other']}").text
    api = client.get(f"/api/v1/devices/{ids['ap']}/cnmaestro-metrics?range=7d").json()
    assert {c["id"] for c in api["charts"]} == {"throughput", "resources", "stations"} and api["sample_count"] == 1
    assert client.get(f"/api/v1/devices/{ids['ap']}/cnmaestro-metrics?range=1y").status_code == 400
    hub = client.get("/integrations").text
    assert 'data-integration="cnmaestro"' in hub and "Collegati a cnMaestro" in hub

    # Wrong credentials: explained, the last data stays.
    cnm.client_for = lambda row, transport_=None: original(row, httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "invalid_client"})))
    page = client.get("/admin/integrations/cnmaestro").text
    failed = client.post("/admin/integrations/cnmaestro/sync", data={"csrf": csrf_from(page)})
    assert "client id/secret" in failed.text
    with SessionLocal() as db:
        assert db.get(Device, ids["ap"]).inventory_data["cnmaestro"]["mac"] == "02:CB:00:00:00:01"
    print("cnMaestro smoke passed")


if __name__ == "__main__":
    main()
