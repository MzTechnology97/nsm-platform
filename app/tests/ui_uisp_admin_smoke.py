"""New interface, UISP administration API with a simulated UISP: save (token kept, never returned), test, manual sync, guards."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import uisp_connector as uisp
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import User
from app.security import hash_password

PASSWORD = "CI-UI-Uisp-Admin-2026-Pass!"
TOKEN = "ci-uisp-placeholder-token"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    original = uisp._http_get
    seen = []

    def fake_http_get(url, token, verify_tls):
        seen.append((url, token))
        assert url == "https://uisp.example.test/nms/api/v2.1/devices" and token == TOKEN
        return [{"identification": {"id": "ci-uisp-1", "mac": "02:15:00:00:20:01", "name": "CI-AP", "model": "LiteAP"}, "overview": {"status": "active"}}]

    uisp._http_get = fake_http_get
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == uisp.UISP_PROVIDER))
        db.add_all([
            User(username=f"ci-uu2-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-uu2-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
        ])
        db.commit()

    client, headers = login(f"ci-uu2-{suffix}")
    base = "/api/ui/v1/admin/integrations/uisp"
    data = client.get(base).json()
    assert data["configured"] is False and data["sync_interval"] > 0
    assert client.post(f"{base}/test", headers=headers).json()["detail"]["code"] == "not_configured"
    assert client.post(f"{base}/sync", headers=headers).json()["detail"]["code"] == "disabled"
    assert client.post(base, json={}).status_code == 403, "CSRF header required"
    assert client.post(base, headers=headers, json={"base_url": "https://uisp.example.test", "enabled": True}).status_code == 422, "token required first"
    assert client.post(base, headers=headers, json={"base_url": "https://uisp.example.test", "api_token": TOKEN, "sync_interval_minutes": 1}).status_code == 422
    saved = client.post(base, headers=headers, json={"base_url": "https://uisp.example.test/", "api_token": TOKEN, "enabled": True, "verify_tls": True,
                                                     "sync_interval_minutes": 30})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["configured"] and body["has_token"] and body["sync_interval"] == 30 and body["base_url"] == "https://uisp.example.test"
    assert TOKEN not in client.get(base).text, "stored token never returned"
    assert client.post(base, headers=headers, json={"base_url": "https://uisp.example.test", "enabled": True, "sync_interval_minutes": 30}).status_code == 200, \
        "an empty token keeps the stored one"

    tested = client.post(f"{base}/test", headers=headers).json()
    assert tested == {"ok": True, "message": "Connessione UISP riuscita: 1 dispositivi leggibili."} and seen
    synced = client.post(f"{base}/sync", headers=headers).json()
    assert synced["status"] == "success" and synced["devices"] == 0, synced
    assert client.get(base).json()["sync"]["last_status"] == "success"
    assert client.get("/app/admin/integrations/uisp").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    tech, tech_headers = login(f"ci-uu2-tech-{suffix}")
    assert tech.get(base).status_code == 403
    assert tech.post(f"{base}/sync", headers=tech_headers).status_code == 403
    uisp._http_get = original
    print("ui uisp admin smoke ok")


if __name__ == "__main__":
    main()
