"""UBNT-09: UniFi Network controller connector — API key, base path detection, sites and paged devices, linking by MAC, admin API."""
import re
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import unifi_connector as unifi
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-UI-UniFi-Admin-2026-Pass!"
KEY = "ci-unifi-placeholder-key"
SITE_A, SITE_B = "88f7af54-98f8-306a-a1c7-c9349722b1f6", "1f0a2c3d-0000-4000-8000-00000000000b"


class FakeController:
    """UniFi OS console (prefix /proxy/network/integration) or self-hosted application (/integration)."""

    def __init__(self, prefix):
        self.prefix, self.calls, self.methods = prefix, [], set()
        filler = [{"id": f"f-{i}", "macAddress": f"02:0f:00:00:{i // 256:02x}:{i % 256:02x}", "name": f"AP-filler-{i}", "model": "U6 Lite",
                   "state": "ONLINE", "features": ["accessPoint"], "firmwareVersion": "6.6.77", "firmwareUpdatable": False} for i in range(230)]
        self.devices = {
            SITE_A: [
                {"id": "d-gw", "macAddress": "02:0f:00:01:00:01", "name": "GW-Sede", "model": "UDM-Pro", "ipAddress": "198.51.100.90", "state": "ONLINE",
                 "features": ["switching"], "firmwareVersion": "4.1.13", "firmwareUpdatable": True, "interfaces": ["ports"]},
                {"id": "d-sw", "macAddress": "02:0f:00:01:00:02", "name": "SW-Rack", "model": "USW-24-PoE", "ipAddress": "198.51.100.91", "state": "OFFLINE",
                 "features": ["switching"], "firmwareVersion": "7.1.26", "firmwareUpdatable": False},
                {"id": "d-ap", "macAddress": "02:0f:00:01:00:03", "name": "AP-Sala", "model": "U6-Pro", "ipAddress": "198.51.100.92", "state": "ONLINE",
                 "features": ["accessPoint"], "firmwareVersion": "6.6.77", "firmwareUpdatable": False},
            ] + filler,
            SITE_B: [{"id": "d-b", "macAddress": "02:0f:00:02:00:01", "name": "AP-Filiale", "model": "U7-Pro", "state": "ONLINE", "features": ["accessPoint"]}],
        }

    def __call__(self, request: httpx.Request):
        path = request.url.path
        self.calls.append(path)
        self.methods.add(request.method)
        if request.headers.get("x-api-key") != KEY:
            return httpx.Response(401, json={"message": "unauthorized"})
        if not path.startswith(self.prefix + "/v1/"):
            return httpx.Response(404)
        rest = path[len(self.prefix) + 3:]
        offset, limit = int(request.url.params.get("offset", 0)), int(request.url.params.get("limit", 25))
        page = lambda rows: httpx.Response(200, json={"offset": offset, "limit": limit, "count": len(rows[offset:offset + limit]),
                                                       "totalCount": len(rows), "data": rows[offset:offset + limit]})
        if rest == "/info":
            return httpx.Response(200, json={"applicationVersion": "9.4.19"})
        if rest == "/sites":
            return page([{"id": SITE_A, "internalReference": "default", "name": "Sede"}, {"id": SITE_B, "internalReference": "b", "name": "Filiale"}])
        match = re.fullmatch(r"/sites/([^/]+)/devices", rest)
        if match:
            return page(self.devices.get(match.group(1), []))
        match = re.fullmatch(r"/sites/([^/]+)/devices/([^/]+)/statistics/latest", rest)
        if match:
            if match.group(2) == "d-ap":
                return httpx.Response(500)
            return httpx.Response(200, json={"uptimeSec": 86400, "cpuUtilizationPct": 12.5, "memoryUtilizationPct": 41.2, "loadAverage1Min": 0.3,
                                             "uplink": {"txRateBps": 1200000, "rxRateBps": 8400000}})
        return httpx.Response(404)


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    assert unifi.normalize_base_url("https://unifi.example.test/proxy/network/integration/v1/") == "https://unifi.example.test"
    assert unifi.normalize_base_url("https://198.51.100.10:8443/integration") == "https://198.51.100.10:8443"
    for bad in ("ftp://x", "https://user:pw@unifi.example.test", "https://localhost", "https://127.0.0.1"):
        try:
            unifi.normalize_base_url(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert [unifi.kind_of(m, f) for m, f in (("UDM-Pro", ["switching"]), ("USW-24-PoE", ["switching"]), ("U6-Pro", ["accessPoint"]),
                                              ("UCG-Ultra", []), ("USG-3P", []), ("Unknown", []))] == ["router", "switch", "wireless_ap", "router", "router", "other"]
    assert unifi.metrics({"cpuUtilizationPct": 140, "memoryUtilizationPct": 30, "uptimeSec": True}) == {"ram_percent": 30.0}, "implausible values dropped"

    fake = FakeController("/proxy/network/integration")
    original = unifi.client_for
    unifi.client_for = lambda row, transport_=None: original(row, httpx.MockTransport(fake))
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == unifi.PROVIDER))
        customer = Customer(name=f"CI UniFi {suffix}", code=f"UF{suffix}")
        db.add(customer)
        db.flush()
        switch = Device(customer_id=customer.id, vendor="ubiquiti", device_type="switch", name=f"TEST-UF-SW-{suffix}", status="unknown",
                        primary_mac="02:0F:00:01:00:02")
        uisp_ap = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_ap", name=f"TEST-UF-AP-{suffix}", status="online",
                         primary_mac="02:0f:00:01:00:03", management_source="uisp", firmware_version="6.5.0", inventory_data={"uisp": {"id": "u-1"}})
        other = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-UF-MT-{suffix}", status="online",
                       primary_mac="02:0f:00:01:00:01")
        db.add_all([switch, uisp_ap, other,
                    User(username=f"ci-uf-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-uf-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)])
        db.commit()
        ids = {"switch": switch.id, "uisp_ap": uisp_ap.id, "other": other.id}

    client, headers = login(f"ci-uf-{suffix}")
    base = "/api/ui/v1/admin/integrations/unifi"
    assert client.get(base).json()["configured"] is False
    assert client.post(f"{base}/test", headers=headers).json()["detail"]["code"] == "not_configured"
    assert client.post(base, json={}).status_code == 403, "CSRF header required"
    assert client.post(base, headers=headers, json={"base_url": "https://unifi.example.test"}).status_code == 422, "API key required"
    saved = client.post(base, headers=headers, json={"base_url": "https://unifi.example.test/proxy/network/integration/v1", "api_key": KEY,
                                                     "verify_tls": False, "enabled": True})
    assert saved.status_code == 200, saved.text
    data = saved.json()
    assert data["configured"] and data["enabled"] and data["base_url"] == "https://unifi.example.test" and data["verify_tls"] is False and data["sites"] == []
    assert KEY not in client.get(base).text, "stored key never returned"
    assert client.post(base, headers=headers, json={"base_url": "https://unifi.example.test", "enabled": True, "verify_tls": False}).status_code == 200, \
        "an empty key keeps the stored one"

    tested = client.post(f"{base}/test", headers=headers).json()
    assert tested["ok"] is True and "9.4.19" in tested["message"] and "2 siti" in tested["message"], tested
    page = client.get(base).json()
    assert page["api_prefix"] == "/proxy/network/integration" and [s["name"] for s in page["known_sites"]] == ["Sede", "Filiale"]

    synced = client.post(f"{base}/sync", headers=headers).json()
    assert synced["status"] == "success" and synced["sites"] == 2 and synced["unifi_devices"] == 234, synced
    assert synced["linked"] == 2 and synced["new_links"] == 2 and synced["not_in_nsm"] == 232, synced
    assert fake.methods == {"GET"}, "read-only: nothing is written to the controller"
    with SessionLocal() as db:
        sw, ap, mt = db.get(Device, ids["switch"]), db.get(Device, ids["uisp_ap"]), db.get(Device, ids["other"])
        meta = sw.inventory_data["unifi"]
        assert meta["id"] == "d-sw" and meta["site"] == "Sede" and meta["kind"] == "switch" and meta["model"] == "USW-24-PoE", meta
        assert meta["metrics"] == {"cpu_percent": 12.5, "ram_percent": 41.2, "uptime_seconds": 86400, "load_1m": 0.3, "uplink_rx_bps": 8400000,
                                   "uplink_tx_bps": 1200000}, meta["metrics"]
        assert sw.status == "offline" and sw.firmware_version == "7.1.26" and sw.management_ip == "198.51.100.91" and sw.model == "USW-24-PoE"
        assert ap.inventory_data["unifi"]["mac"] and ap.inventory_data["unifi"]["metrics"] == {}, "statistics error: no metrics"
        assert ap.firmware_version == "6.5.0" and ap.management_source == "uisp", "UISP stays the source of a UISP-managed device"
        assert "unifi" not in (mt.inventory_data or {}), "only Ubiquiti devices are linked"
        assert len(list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == "UNIFI_DEVICE_LINKED", AuditEvent.device_id.in_(list(ids.values())))))) == 2
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "UNIFI_CONFIGURED"))
    page = client.get(base).json()
    assert page["last_sync"]["statistics_error"] and page["last_sync"]["version"] == "9.4.19" and len(page["devices"]) == 234
    gateway = next(d for d in page["devices"] if d["id"] == "d-gw")
    assert gateway["kind"] == "router" and gateway["firmware_updatable"] is True and gateway["in_nsm"] is False and gateway["status"] == "online"
    assert next(d for d in page["devices"] if d["id"] == "d-sw")["in_nsm"] is True
    again = client.post(f"{base}/sync", headers=headers).json()
    assert again["linked"] == 2 and again["new_links"] == 0, "links are kept, not repeated"

    # Site filter: only the chosen site is read.
    client.post(base, headers=headers, json={"base_url": "https://unifi.example.test", "enabled": True, "verify_tls": False, "sites": [SITE_B]})
    before = len(fake.calls)
    only_b = client.post(f"{base}/sync", headers=headers).json()
    assert only_b["unifi_devices"] == 1 and only_b["sites"] == 2, only_b
    assert all(f"/sites/{SITE_A}/" not in call for call in fake.calls[before:]), fake.calls[before:]
    hub = client.get("/api/ui/v1/integrations").json()["unifi"]
    assert hub["state"] == "active" and hub["devices"] == 1 and hub["version"] == "9.4.19", hub

    # Self-hosted Network Application: the other base path is found and stored.
    self_hosted = FakeController("/integration")
    unifi.client_for = lambda row, transport_=None: original(row, httpx.MockTransport(self_hosted))
    client.post(base, headers=headers, json={"base_url": "https://198.51.100.10:8443", "enabled": True, "verify_tls": False, "sites": []})
    assert client.get(base).json()["api_prefix"] is None, "a new address forgets the old path"
    tested = client.post(f"{base}/test", headers=headers).json()
    assert tested["ok"] and client.get(base).json()["api_prefix"] == "/integration", tested
    assert "/proxy/network/integration/v1/info" in self_hosted.calls and "/integration/v1/info" in self_hosted.calls

    # Wrong key: a clear message, nothing linked.
    with SessionLocal() as db:
        row = unifi.connection_row(db)
        row.secret_encrypted = unifi.encrypt_text('{"api_key": "wrong"}')
        db.commit()
    denied = client.post(f"{base}/test", headers=headers).json()
    assert denied["ok"] is False and "chiave API" in denied["message"], denied
    assert client.post(f"{base}/sync", headers=headers).json()["status"] == "failed"
    assert client.get(base).json()["last_test_status"] == "failed"
    assert client.get("/app/admin/integrations/unifi").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    tech, tech_headers = login(f"ci-uf-tech-{suffix}")
    assert tech.get(base).status_code == 403
    assert tech.post(f"{base}/sync", headers=tech_headers).status_code == 403
    unifi.client_for = original
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == unifi.PROVIDER))
        db.commit()
    print("unifi connector smoke ok")


if __name__ == "__main__":
    main()
