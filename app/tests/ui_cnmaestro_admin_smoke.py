"""New interface, cnMaestro administration API with a simulated cnMaestro: save (secret kept, never returned), test, sync, guards."""
import re
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import cnmaestro_connector as cnm
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-UI-cnMaestro-Admin-2026-Pass!"
SECRET = "ci-cnmaestro-placeholder-secret"


def fake(request: httpx.Request):
    path = request.url.path
    if path == "/api/v1/access/token":
        return httpx.Response(200, json={"access_token": "ci-token", "expires_in": 3600})
    if path == "/api/v1/devices":
        rows = [{"mac": "02:CB:00:00:10:01", "serial_number": "CB-UI-1", "name": "AP-UI", "ip": "198.51.100.81", "status": "online", "product": "ePMP 3000"}]
        offset = int(request.url.params.get("offset", 0))
        return httpx.Response(200, json={"data": rows[offset:offset + 100], "paging": {"total": len(rows), "offset": offset}})
    if path == "/api/v1/devices/statistics":
        return httpx.Response(200, json={"data": [], "paging": {"total": 0, "offset": 0}})
    return httpx.Response(404)


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    original = cnm.client_for
    cnm.client_for = lambda row, transport_=None: original(row, httpx.MockTransport(fake))
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == cnm.PROVIDER))
        customer = Customer(name=f"CI UI cnMaestro {suffix}", code=f"UC{suffix}")
        db.add(customer)
        db.flush()
        db.add(Device(customer_id=customer.id, vendor="cambium", device_type="ap", name=f"TEST-UC-AP-{suffix}", status="online",
                      primary_mac="02:CB:00:00:10:01", inventory_data={"manufacturer": "cambium"}))
        db.add_all([
            User(username=f"ci-ucn-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-ucn-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
        ])
        db.commit()

    client, headers = login(f"ci-ucn-{suffix}")
    base = "/api/ui/v1/admin/integrations/cnmaestro"
    assert client.get(base).json()["configured"] is False
    assert client.post(f"{base}/test", headers=headers).json()["detail"]["code"] == "not_configured"
    assert client.post(base, json={}).status_code == 403, "CSRF header required"
    bad = client.post(base, headers=headers, json={"base_url": "ftp://x", "client_id": "ci", "client_secret": SECRET})
    assert bad.status_code == 422
    assert client.post(base, headers=headers, json={"base_url": "https://cnmaestro.example.test", "client_id": "ci"}).status_code == 422, "secret required"
    saved = client.post(base, headers=headers, json={"base_url": "https://cnmaestro.example.test/api/v1/", "client_id": "ci-client", "client_secret": SECRET,
                                                     "verify_tls": True, "enabled": True})
    assert saved.status_code == 200, saved.text
    data = saved.json()
    assert data["configured"] and data["enabled"] and data["base_url"] == "https://cnmaestro.example.test" and data["client_id"] == "ci-client"
    assert SECRET not in client.get(base).text, "stored secret never returned"
    kept = client.post(base, headers=headers, json={"base_url": "https://cnmaestro.example.test", "client_id": "ci-client", "enabled": True})
    assert kept.status_code == 200, "an empty secret keeps the stored one"

    tested = client.post(f"{base}/test", headers=headers).json()
    assert tested["ok"] is True and "Connessione riuscita" in tested["message"], tested
    assert client.get(base).json()["last_test_status"] == "success"
    synced = client.post(f"{base}/sync", headers=headers).json()
    assert synced["status"] == "success" and synced["cnmaestro_devices"] == 1, synced
    after = client.get(base).json()
    assert after["last_sync"]["cnmaestro_devices"] == 1 and after["last_sync_at"]
    assert client.get("/app/admin/integrations/cnmaestro").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    tech, tech_headers = login(f"ci-ucn-tech-{suffix}")
    assert tech.get(base).status_code == 403
    assert tech.post(f"{base}/sync", headers=tech_headers).status_code == 403
    cnm.client_for = original
    print("ui cnmaestro admin smoke ok")


if __name__ == "__main__":
    main()
