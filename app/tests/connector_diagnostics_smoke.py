"""Connector compatibility report (UISP, cnMaestro): GET-only probes, expected fields found/missing, no values in the report, audited, admin only."""
import re
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import cnmaestro_connector as cnm
from app import uisp_operations as ops
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-Connector-Diag-2026"
SECRET_VALUES = ("02:6B:00:00:0D:01", "192.0.2.77", "CPE Rooftop Secret", "SN-SECRET-1")


class FakeUisp:
    """UISP answering with a slightly different schema than the one NSM expects (no overview.signal, ipAddress under overview)."""

    def __init__(self, device_id):
        self.id = device_id
        self.methods = []

    def __call__(self, req: httpx.Request):
        self.methods.append(req.method)
        path = req.url.path.removeprefix("/nms/api/v2.1")
        device = {"identification": {"id": self.id, "mac": SECRET_VALUES[0], "displayName": SECRET_VALUES[2], "model": "LBE-5AC-Gen2",
                                     "serialNumber": SECRET_VALUES[3], "firmwareVersion": "8.7.11", "site": {"id": "site-1"}},
                  "overview": {"status": "active", "lastSeen": "2026-10-21T10:00:00Z", "ipAddress": SECRET_VALUES[1], "cpu": 12, "ram": 40,
                               "uptime": 7200, "wirelessSignal": -61},
                  "firmware": {"current": "8.7.11", "latest": "8.7.13", "compatible": True},
                  "interfaces": {SECRET_VALUES[0]: {"up": True}}}
        if req.method != "GET":
            return httpx.Response(405)
        if path == "/devices":
            return httpx.Response(200, json=[device])
        if path == f"/devices/{self.id}":
            return httpx.Response(200, json=device)
        if path == f"/devices/{self.id}/interfaces":
            return httpx.Response(200, json=[{"identification": {"name": "eth0", "type": "ethernet"}, "status": {"status": "active", "plugged": True}}])
        if path == f"/devices/{self.id}/backups":
            return httpx.Response(404)
        if path == "/logs":
            return httpx.Response(200, json={"items": [{"timestamp": 1, "level": "info", "message": "x"}]})
        return httpx.Response(404)


def fake_cnmaestro(req: httpx.Request):
    path = req.url.path
    if path == "/api/v2/access/token":
        return httpx.Response(200, json={"access_token": "ci-token"})
    if path == "/api/v2/devices":
        return httpx.Response(200, json={"data": [{"mac": "02:6B:00:00:0D:02", "serial_number": "SN-2", "name": "ePMP", "ip": "198.51.100.40",
                                                   "status": "online", "product": "ePMP 3000", "software_version": "4.7.1", "type": "epmp"}],
                                         "paging": {"total": 1}})
    if path == "/api/v2/devices/statistics":
        return httpx.Response(403, json={"error": "forbidden"})
    return httpx.Response(404)


def main():
    suffix = uuid.uuid4().hex[:6]
    uisp_id = f"u-diag-{suffix}"
    fake = FakeUisp(uisp_id)
    ops._client = lambda connection: httpx.Client(transport=httpx.MockTransport(fake), headers={"x-auth-token": "ci"})
    original = cnm.client_for
    cnm.client_for = lambda row, transport=None: original(row, httpx.MockTransport(fake_cnmaestro))
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_(["uisp", "cnmaestro"])))
        db.add(ConnectorIntegration(provider="uisp", name="UISP", base_url="https://uisp.example.test", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text("ci-uisp-token"), settings={}))
        db.add(ConnectorIntegration(provider="cnmaestro", name="cnMaestro", base_url="https://cnm.example.test", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text('{"client_id": "ci", "client_secret": "ci-secret"}'), settings={}))
        customer = Customer(name=f"CI Diag {suffix}", code=f"DG{suffix}")
        db.add(customer)
        db.flush()
        db.add(Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-DG-{suffix}", external_device_id=uisp_id,
                      management_source="uisp", status="online"))
        db.add(User(username=f"ci-dg-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.add(User(username=f"ci-dg-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True))
        db.commit()

    def login(username):
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        return client

    admin, tech = login(f"ci-dg-{suffix}"), login(f"ci-dg-tech-{suffix}")
    assert tech.post("/api/ui/v1/admin/integrations/uisp/diagnostics").status_code == 403
    assert admin.get("/api/ui/v1/admin/integrations/other/diagnostics").status_code == 404
    assert admin.get("/api/ui/v1/admin/integrations/uisp/diagnostics").json() == {"report": None}

    report = admin.post("/api/ui/v1/admin/integrations/uisp/diagnostics").json()["report"]
    assert set(fake.methods) == {"GET"}, "diagnostics never writes"
    endpoints = {e["name"]: e for e in report["endpoints"]}
    assert endpoints["GET /devices"]["status"] == 200 and endpoints["GET /devices"]["rows"] == 1
    fields = {f["field"]: f for f in endpoints["GET /devices"]["fields"]}
    assert fields["mac"]["state"] == "ok" and fields["mac"]["path"] == "identification.mac"
    assert fields["IP"]["path"] == "overview.ipAddress" and fields["metrica signal"]["state"] == "missing"
    assert fields["firmware più recente"]["state"] == "ok"
    assert endpoints["GET /devices/{id}/backups"]["status"] == 404 and "GET /devices/{id}/backups" in report["failed_endpoints"]
    assert "GET /devices: metrica signal" in report["missing"] and report["sample_device"] == "collegato in NSM"
    # The report lists the key names actually received (so the right one can be mapped), never values; identifier keys are masked.
    keys = " ".join(endpoints["GET /devices"]["keys"])
    assert "overview.wirelessSignal:number" in keys and "interfaces.<id>:object" in keys
    text = report["text"]
    for value in SECRET_VALUES + (uisp_id,):
        assert value not in text and value not in str(report), value
    assert admin.get("/api/ui/v1/admin/integrations/uisp/diagnostics").json()["report"]["at"] == report["at"]

    cn = admin.post("/api/ui/v1/admin/integrations/cnmaestro/diagnostics").json()["report"]
    cn_endpoints = {e["name"]: e for e in cn["endpoints"]}
    assert cn_endpoints["GET /devices"]["status"] == 200 and {f["field"]: f["state"] for f in cn_endpoints["GET /devices"]["fields"]}["firmware"] == "ok"
    assert cn_endpoints["GET /devices/statistics"]["status"] is None and cn_endpoints["GET /devices/statistics"]["error"]
    assert cn["failed_endpoints"] == ["GET /devices/statistics"] and "198.51.100.40" not in cn["text"]

    with SessionLocal() as db:
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == "CONNECTOR_DIAGNOSTICS_RUN").order_by(AuditEvent.timestamp.desc()).limit(2)))
        assert {e.details["provider"] for e in events} == {"uisp", "cnmaestro"}
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider.in_(["uisp", "cnmaestro"])))
        db.commit()
    cnm.client_for = original
    print("Connector diagnostics smoke passed")


if __name__ == "__main__":
    main()
