"""New interface, GenieACS administration API with a simulated NBI: save (secret kept, never returned), built-in link, test, guards."""
import os
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import genieacs_connector as acs
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import User
from app.security import hash_password

PASSWORD = "CI-UI-GenieACS-Admin-2026-Pass!"
SECRET = "ci-genieacs-placeholder-secret"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    original = acs._http_get
    calls = []

    def fake_http_get(connection, *, query, projection):
        calls.append(query)
        if "fail" in connection.base_url:
            raise acs.GenieAcsConnectorError("NBI non raggiungibile (simulato).")
        return []

    acs._http_get = fake_http_get
    os.environ.pop("GENIEACS_INTERNAL_NBI_URL", None)
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == acs.GENIEACS_PROVIDER))
        db.add_all([
            User(username=f"ci-uga-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-uga-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
        ])
        db.commit()

    client, headers = login(f"ci-uga-{suffix}")
    base = "/api/ui/v1/admin/integrations/genieacs"
    data = client.get(base).json()
    assert data["configured"] is False and data["internal_url"] is None and data["mac_parameter_paths"] == list(acs.DEFAULT_MAC_PARAMETER_PATHS)
    assert client.post(f"{base}/internal", headers=headers).json()["detail"]["code"] == "no_internal"
    assert client.post(f"{base}/test", headers=headers).json()["detail"]["code"] == "not_configured"
    assert client.post(base, json={}).status_code == 403, "CSRF header required"
    assert client.post(base, headers=headers, json={"base_url": "http://localhost:7557"}).status_code == 422, "loopback refused"
    saved = client.post(base, headers=headers, json={
        "base_url": "https://genieacs.example.test:7557", "auth_mode": "bearer", "auth_secret": SECRET,
        "mac_parameter_paths": "Device.Ethernet.Interface.1.MACAddress\nDevice.Ethernet.Interface.1.MACAddress", "online_window_minutes": 1,
        "enabled": True, "verify_tls": True})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["auth_mode"] == "bearer" and body["mac_parameter_paths"] == ["Device.Ethernet.Interface.1.MACAddress"] and body["online_window_minutes"] == 5
    assert SECRET not in client.get(base).text, "stored secret never returned"

    tested = client.post(f"{base}/test", headers=headers).json()
    assert tested == {"ok": True, "message": "Connessione GenieACS NBI riuscita in modalità read-only."} and calls
    assert client.get(base).json()["last_test_status"] == "success"
    assert client.post(base, headers=headers, json={"base_url": "https://genieacs-fail.example.test", "auth_mode": "bearer", "enabled": True}).status_code == 200
    failed = client.post(f"{base}/test", headers=headers).json()
    assert failed["ok"] is False and "simulato" in failed["message"]

    os.environ["GENIEACS_INTERNAL_NBI_URL"] = "http://genieacs-nbi.example.test:7557"
    linked = client.post(f"{base}/internal", headers=headers).json()
    assert linked["uses_internal"] is True and linked["auth_mode"] == "none" and linked["enabled"] is True
    os.environ.pop("GENIEACS_INTERNAL_NBI_URL", None)
    assert client.get("/app/admin/integrations/genieacs").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    tech, tech_headers = login(f"ci-uga-tech-{suffix}")
    assert tech.get(base).status_code == 403
    assert tech.post(f"{base}/test", headers=tech_headers).status_code == 403
    acs._http_get = original
    print("ui genieacs admin smoke ok")


if __name__ == "__main__":
    main()
