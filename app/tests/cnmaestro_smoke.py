"""VEND-02: cnMaestro connector on API v2 — OAuth2 (v1 login fallback), paged inventory and statistics, linking by MAC/serial,
adoption of hand-made Cambium records, metrics history and pages."""
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app import cnmaestro_connector as cnm
from app.cnmaestro_models import CambiumMetricSample
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-cnMaestro-2026"


DEPRECATION = {"message": "The v1 version of this API is deprecated and must be changed to v2.", "sunset": "3.2.0"}


class FakeCnMaestro:
    """cnMaestro northbound API: v2 inventory and statistics; v1 answers carry the deprecation note.
    ``v2_login=False`` emulates a cnMaestro without the v2 token endpoint (404)."""

    def __init__(self, devices, stats, v2_login=True):
        self.devices, self.stats, self.calls, self.v2_login = devices, stats, [], v2_login

    def __call__(self, request: httpx.Request):
        path = request.url.path
        self.calls.append(path)
        if path in ("/api/v2/access/token", "/api/v1/access/token"):
            if path == "/api/v2/access/token" and not self.v2_login:
                return httpx.Response(404, text="Not Found")
            assert request.method == "POST" and b"grant_type=client_credentials" in request.content
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "ci-token", "expires_in": 3600})
        assert request.headers.get("authorization") == "Bearer ci-token"
        offset, limit = int(request.url.params.get("offset", 0)), int(request.url.params.get("limit", 100))
        version, _, rest = path.removeprefix("/api/").partition("/")
        rows = self.devices if rest == "devices" else self.stats if rest == "devices/statistics" else None
        if rows is None or version not in ("v1", "v2"):
            return httpx.Response(404)
        body = {"data": rows[offset:offset + limit], "paging": {"total": len(rows), "limit": limit, "offset": offset}}
        return httpx.Response(200, json={**body, "deprecation": DEPRECATION} if version == "v1" else body)


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    assert cnm.normalize_base_url("https://cnmaestro.example.test/api/v1/") == "https://cnmaestro.example.test"
    assert cnm.normalize_base_url("https://cnmaestro.example.test/api/v2") == "https://cnmaestro.example.test"
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
    # API v2 Wi-Fi AP (cnPilot): per-band ``radios`` list instead of radio.24ghz / radio.5ghz.
    wifi = {"cpu": 5, "memory": 30, "status": "online", "status_time": 7200,
            "radios": [{"band": "2.4GHz", "num_clients": 3, "tx_bps": 1000, "rx_bps": 200}, {"band": "5GHz", "num_clients": 9, "tx_bps": 9000, "rx_bps": 800}]}
    assert cnm.metrics(wifi) == {"cpu_percent": 5.0, "ram_percent": 30.0, "stations": 12, "dl_throughput_bps": 10000, "ul_throughput_bps": 1000,
                                 "uptime_seconds": 7200}, cnm.metrics(wifi)
    v1_wifi = {"radio": {"24ghz": {"num_clients": 3, "tx_bps": 1000}, "5ghz": {"num_clients": 9, "tx_bps": 9000}}}
    assert cnm.metrics(v1_wifi) == {"stations": 12, "dl_throughput_bps": 10000}, "the v1 shape is still read"

    suffix = uuid.uuid4().hex[:6]
    filler = [{"mac": f"02:CB:00:01:{i // 256:02X}:{i % 256:02X}", "name": f"filler-{i}", "status": "online"} for i in range(150)]
    devices = filler + [
        {"mac": "02:CB:00:00:00:01", "serial_number": "CB-AP-1", "name": "AP-Tower-1", "ip": "198.51.100.71", "status": "online", "product": "ePMP 3000",
         "software_version": "4.7.0.1", "type": "epmp", "network": "Rete Nord", "tower": "Torre 1"},
        {"mac": "02:cb:00:00:00:99", "msn": "cb-sm-9", "name": "SM-Rossi", "ip": "198.51.100.72", "status": "offline", "product": "Force 300-25",
         "software_version": "4.6.2", "type": "epmp"},
        # The operator's case: ePMP Force 400 created by hand (generic, corrected to Cambium later), manual source, no external id.
        {"mac": "02:CB:00:00:00:40", "msn": "CB-F400-1", "name": "SM-Force400", "ip": "198.51.100.74", "status": "online", "product": "ePMP Force 400",
         "software_version": "5.10.4", "type": "epmp", "mode": "sm", "ap_mac": "02:CB:00:00:00:01", "config": {"version": "7"}},
    ]
    stats = [{"mac": "02:CB:00:00:00:01", "cpu": 23, "memory": 51, "connected_sms": 17, "uptime": 360000, "radio": {"dl_throughput": 88000000, "ul_throughput": 21000000}},
             {"mac": "02:CB:00:00:00:99", "radio": {"dl_rssi": -64, "dl_snr": 27}}]
    fake = FakeCnMaestro(devices, stats)
    transport = httpx.MockTransport(fake)
    original = cnm.client_for
    cnm.client_for = lambda row, transport_=None: original(row, transport_ or transport)

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
        force = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_cpe", name=f"TEST-CN-F400-{suffix}", primary_mac="02:CB:00:00:00:40",
                       serial_number="CB-F400-1", model="ePMP Force 400", firmware_version="5.10.3", management_ip="198.51.100.74",
                       management_source="manual", inventory_source="manual", status="online", inventory_data={"manufacturer": "cambium"})
        db.add_all([ap, sm, lost, other, force])
        db.add(User(username=f"ci-cn-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {"ap": ap.id, "sm": sm.id, "lost": lost.id, "other": other.id, "force": force.id}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-cn-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    admin_api = "/api/ui/v1/admin/integrations/cnmaestro"
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    saved = client.post(admin_api, headers=headers, json={"base_url": "https://cnmaestro.example.test", "client_id": "ci-client",
                                                          "client_secret": "ci-secret-value", "verify_tls": True, "enabled": True})
    assert saved.status_code == 200 and "ci-secret-value" not in saved.text, saved.text
    with SessionLocal() as db:
        assert "ci-secret-value" not in cnm.connection_row(db).secret_encrypted
    tested = client.post(f"{admin_api}/test", headers=headers).json()
    assert tested["ok"] is True, tested
    synced = client.post(f"{admin_api}/sync", headers=headers).json()
    assert synced["status"] == "success" and synced["cnmaestro_devices"] == 153, synced
    assert synced["adopted"] >= 3 and synced["deprecation"] is None, synced
    assert fake.calls.count("/api/v2/devices") >= 3, "inventory read in pages (test call + two pages)"
    assert "/api/v2/devices/statistics" in fake.calls and "/api/v2/access/token" in fake.calls
    assert not [c for c in fake.calls if c.startswith("/api/v1/")], f"no v1 call left: {fake.calls}"

    with SessionLocal() as db:
        ap, sm, lost, other = (db.get(Device, ids[k]) for k in ("ap", "sm", "lost", "other"))
        assert ap.inventory_data["cnmaestro"]["mac"] == "02:CB:00:00:00:01" and ap.management_ip == "198.51.100.71" and ap.firmware_version == "4.7.0.1"
        assert ap.model == "ePMP 3000" and ap.status == "online" and ap.inventory_data["cnmaestro"]["tower"] == "Torre 1"
        assert sm.inventory_data["cnmaestro"]["mac"] == "02:CB:00:00:00:99" and sm.status == "offline", "linked by serial"
        assert "cnmaestro" not in (lost.inventory_data or {}) and "cnmaestro" not in (other.inventory_data or {}), "only Cambium devices with a match"
        assert other.management_source == "manual" and other.external_device_id is None and lost.management_source == "manual"
        force = db.get(Device, ids["force"])
        assert force.inventory_data["cnmaestro"]["mac"] == "02:CB:00:00:00:40" and force.firmware_version == "5.10.4", "hand-made Cambium record linked"
        assert (force.management_source, force.inventory_source, force.external_device_id) == ("cnmaestro", "cnmaestro", "02:CB:00:00:00:40"), "adopted"
        assert force.inventory_last_verified_at is not None and force.inventory_data["manufacturer"] == "cambium"
        assert (ap.management_source, ap.external_device_id) == ("cnmaestro", "02:CB:00:00:00:01")
        linked_event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "CNMAESTRO_DEVICE_LINKED", AuditEvent.device_id == force.id))
        assert linked_event.details["changes"]["management_source"] == {"before": "manual", "after": "cnmaestro"}, linked_event.details
        samples = {s.device_id: s for s in db.scalars(select(CambiumMetricSample).where(CambiumMetricSample.device_id.in_([ap.id, sm.id])))}
        assert samples[ap.id].stations == 17 and samples[ap.id].dl_throughput_bps == 88000000 and samples[sm.id].signal_dbm == -64.0
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "CNMAESTRO_DEVICE_LINKED", AuditEvent.device_id == ap.id)) is not None
        again = cnm.sync(db, now=utcnow() + timedelta(minutes=1), transport=transport)
        assert again["samples"] == 0, "at most one sample every few minutes"
        assert db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.event_type == "CNMAESTRO_DEVICE_LINKED", AuditEvent.device_id == ids["force"])) == 1,             "adoption is audited once"

    # A cnMaestro without the v2 login: the token comes from /api/v1/access/token, everything else stays v2.
    old_login = FakeCnMaestro(devices, stats, v2_login=False)
    with SessionLocal() as db:
        assert cnm.sync(db, now=utcnow() + timedelta(minutes=2), transport=httpx.MockTransport(old_login))["status"] == "success"
    assert old_login.calls[:2] == ["/api/v2/access/token", "/api/v1/access/token"], old_login.calls
    assert [c for c in old_login.calls if c.startswith("/api/v1/")] == ["/api/v1/access/token"], old_login.calls

    # A deprecation note in an answer is kept in the last sync result.
    def deprecated(request):
        response = fake(request)
        if request.url.path == "/api/v2/devices/statistics":
            return httpx.Response(200, json={**response.json(), "deprecation": {"message": "Use v3.", "sunset": "9.9.9"}})
        return response
    with SessionLocal() as db:
        noted = cnm.sync(db, now=utcnow() + timedelta(minutes=3), transport=httpx.MockTransport(deprecated))
        assert noted["status"] == "success" and noted["deprecation"] == "Use v3.", noted
        assert cnm.connection_row(db).settings["last_sync"]["deprecation"] == "Use v3."

    device_page = client.get(f"/api/ui/v1/devices/{ids['sm']}/cnmaestro").json()
    assert device_page["linked"] is True and device_page["can"]["metrics"] is True
    assert {"field": "signal_dbm", "unit": "dBm", "value": -64.0} in device_page["metrics"], device_page["metrics"]
    def tabs(device_id):
        return {tab["key"] for tab in client.get(f"/api/ui/v1/devices/{device_id}").json()["tabs"]}

    assert "cnmaestro" in tabs(ids["ap"]), "cnMaestro tab on Cambium devices"
    assert "cnmaestro" not in tabs(ids["other"])
    api = client.get(f"/api/v1/devices/{ids['ap']}/cnmaestro-metrics?range=7d").json()
    assert {c["id"] for c in api["charts"]} == {"throughput", "resources", "stations"} and api["sample_count"] == 1
    assert client.get(f"/api/v1/devices/{ids['ap']}/cnmaestro-metrics?range=1y").status_code == 400
    hub = client.get("/api/ui/v1/integrations").json()["cnmaestro"]
    assert hub["state"] == "active" and hub["linked"] >= 1, hub

    # Wrong credentials: explained, the last data stays.
    cnm.client_for = lambda row, transport_=None: original(row, httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "invalid_client"})))
    failed = client.post(f"{admin_api}/sync", headers=headers).json()
    assert failed["status"] == "failed" and "client id/secret" in (failed["error"] or ""), failed
    with SessionLocal() as db:
        assert db.get(Device, ids["ap"]).inventory_data["cnmaestro"]["mac"] == "02:CB:00:00:00:01"
    print("cnMaestro smoke passed")


if __name__ == "__main__":
    main()
