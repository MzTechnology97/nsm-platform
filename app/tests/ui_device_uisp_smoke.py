"""New interface, UISP page of a device: data, exact-MAC preview, associate/refresh, metrics, reboot by name, backup, interfaces, events."""
import gzip
import re
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import uisp_connector as uisp
from app import uisp_operations as ops
from app.integration_models import ConnectorIntegration
from app.db import SessionLocal
from app.entrypoint import app
from app.models import BackupRun, Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-UI-Device-UISP-2026"
TOKEN = "ci-uisp-token"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def code(response):
    assert response.status_code == 422, response.text
    return response.json()["detail"]["code"]


def main():
    suffix = uuid.uuid4().hex[:6]
    uisp_id = f"u-ui-{suffix}"
    mac = f"02:6B:{suffix[0:2]}:{suffix[2:4]}:{suffix[4:6]}:01".upper()
    backup = gzip.compress(b"airos-config-ci\n" * 20)
    backups = [{"id": "b-old", "timestamp": "2026-10-01T03:00:00Z"}]

    def fake_http_get(url, token, verify_tls):
        return [{"identification": {"id": uisp_id, "mac": mac.lower(), "displayName": f"cpe-{suffix}", "firmwareVersion": "8.7.19", "model": "LBE-5AC-Gen2"},
                 "overview": {"status": "active", "cpu": 12, "ram": 40, "signal": -61, "uptime": 900000}, "ipAddress": "198.51.100.61"}]

    def fake_ops(req: httpx.Request):
        path = req.url.path.removeprefix("/nms/api/v2.1")
        device = f"/devices/{uisp_id}"
        if req.method == "GET" and path == device:
            return httpx.Response(200, json={"overview": {"uptime": 900000}})
        if req.method == "POST" and path == f"{device}/restart":
            return httpx.Response(200, json={"result": True})
        if req.method == "GET" and path == f"{device}/backups":
            return httpx.Response(200, json=backups)
        if req.method == "POST" and path == f"{device}/backups":
            backups.append({"id": "b-new", "timestamp": "2026-10-15T10:00:00Z"})
            return httpx.Response(201, json={})
        if req.method == "GET" and path.startswith(f"{device}/backups/"):
            return httpx.Response(200, content=backup, headers={"Content-Type": "application/octet-stream"})
        if req.method == "GET" and path == f"{device}/interfaces":
            return httpx.Response(200, json=[{"identification": {"name": "eth0", "type": "ethernet"}, "enabled": True, "status": {"status": "active"},
                                              "statistics": {"rxrate": 1000000, "txrate": 200000}}])
        if req.method == "GET" and path == "/logs":
            return httpx.Response(200, json={"items": [{"timestamp": "2026-10-15T09:00:00Z", "level": "warning", "type": "device", "message": "Signal degraded"}]})
        return httpx.Response(404)

    uisp._http_get = fake_http_get
    ops._client = lambda connection: httpx.Client(transport=httpx.MockTransport(fake_ops), headers={"x-auth-token": TOKEN})

    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        db.add(ConnectorIntegration(provider="uisp", name="UISP", base_url="https://uisp.example.test", verify_tls=True, is_enabled=True,
                                    secret_encrypted=encrypt_text(TOKEN), settings={}))
        customer = Customer(name=f"CI UI Device UISP {suffix}", code=f"DU{suffix}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-DU-{suffix}", display_name=f"TEST-DU-{suffix}",
                        primary_mac=mac, status="pending_link")
        mikrotik = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DU-MT-{suffix}", status="online")
        db.add_all([device, mikrotik])
        db.add_all([
            User(username=f"ci-du-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-du-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        ids = {"device": str(device.id), "mikrotik": str(mikrotik.id)}

    admin, admin_csrf = login(f"ci-du-{suffix}")
    auditor, auditor_csrf = login(f"ci-du-aud-{suffix}")
    base = f"/api/ui/v1/devices/{ids['device']}/uisp"
    assert TestClient(app).get(base).status_code == 401
    assert admin.get(f"/api/ui/v1/devices/{ids['mikrotik']}/uisp").status_code == 404, "Ubiquiti only"

    tab = auditor.get(base).json()
    assert tab["record"]["mac"] == mac and tab["record"]["external_id"] is None and tab["connection"]["enabled"] is True
    assert tab["metrics"] is None and tab["can"]["write"] is False

    # Preview by exact MAC (read-only), association needs devices.write.
    preview = auditor.post(f"{base}/preview", headers=auditor_csrf).json()
    assert preview["external_id"] == uisp_id and preview["model"] == "LBE-5AC-Gen2"
    assert auditor.post(f"{base}/associate", headers=auditor_csrf).status_code == 403
    assert admin.post(f"{base}/associate").status_code == 403, "CSRF header required"
    assert admin.post(f"{base}/associate", headers=admin_csrf).json() == {"ok": True}
    tab = admin.get(base).json()
    assert tab["record"]["external_id"] == uisp_id and tab["metrics"] is not None and tab["can"]["reboot"] is True
    assert admin.post(f"{base}/refresh", headers=admin_csrf).json() == {"ok": True}
    assert admin.get(f"{base}/metrics", params={"range": "7d"}).json()["range"] == "7d"
    assert code(admin.get(f"{base}/metrics", params={"range": "1y"})) == "range"

    # Operations: reboot needs the device name, backup archives the UISP file, interfaces and events are stored.
    assert code(admin.post(f"{base}/reboot", headers=admin_csrf, json={"confirm_name": "wrong"})) == "confirm_name"
    assert admin.post(f"{base}/reboot", headers=admin_csrf, json={"confirm_name": f"TEST-DU-{suffix}"}).json() == {"ok": True}
    assert admin.get(base).json()["reboot"]["status"] == "requested"
    assert code(admin.post(f"{base}/reboot", headers=admin_csrf, json={"confirm_name": f"TEST-DU-{suffix}"})) == "uisp", "a reboot is already being verified"
    assert admin.post(f"{base}/backup", headers=admin_csrf).json()["ok"] is True
    with SessionLocal() as db:
        run = db.scalar(select(BackupRun).where(BackupRun.device_id == uuid.UUID(ids["device"]), BackupRun.backup_type == "uisp_backup"))
        assert run.status == "success"
    assert admin.post(f"{base}/interfaces", headers=admin_csrf).json()["count"] == 1
    assert admin.post(f"{base}/events", headers=admin_csrf).json()["count"] == 1
    tab = auditor.get(base).json()
    assert tab["interfaces"]["items"][0]["name"] == "eth0" and tab["events"]["items"][0]["message"] == "Signal degraded"
    assert tab["last_backup"]["status"] == "success"
    assert admin.post(f"{base}/explode", headers=admin_csrf).status_code == 404

    assert admin.get(f"/app/devices/{ids['device']}/uisp").status_code in (200, 503)
    print("ui device uisp smoke ok")


if __name__ == "__main__":
    main()
