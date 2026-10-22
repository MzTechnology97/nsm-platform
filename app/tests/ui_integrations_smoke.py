"""New interface, integrations hub API: real state per data source, no secrets, visible to every signed-in user."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import User
from app.security import hash_password

PASSWORD = "CI-UI-Integrations-2026-Pass!"
STORED = "ci-integrations-placeholder"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add_all([
            User(username=f"ci-ui-int-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-ui-int-op-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True),
        ])
        db.commit()

    client = login(f"ci-ui-int-{suffix}")
    data = client.get("/api/ui/v1/integrations").json()
    assert set(data) == {"can_admin", "agent", "uisp", "api", "nvd", "genieacs", "cnmaestro", "ispbilling", "zabbix"} and data["can_admin"] is True
    assert data["agent"]["state"] in ("none", "stale", "partial", "active")
    for key in ("uisp", "nvd", "genieacs", "cnmaestro", "zabbix"):
        assert data[key]["state"] in ("unconfigured", "disabled", "error", "active", "pending"), (key, data[key])

    with SessionLocal() as db:
        nvd = db.query(ConnectorIntegration).filter(ConnectorIntegration.provider == "nvd").one_or_none()
        if nvd is None:
            nvd = ConnectorIntegration(provider="nvd", name="NVD", base_url="https://nvd.example.test", secret_encrypted=STORED, is_enabled=True,
                                       verify_tls=True, settings={})
            db.add(nvd)
        nvd.is_enabled = True
        nvd.settings = {"sync": {"last_status": "success", "last_success_at": "2026-10-14T08:00:00+00:00"}}
        db.commit()
    body = client.get("/api/ui/v1/integrations")
    assert body.json()["nvd"]["state"] == "active" and body.json()["nvd"]["last_success_at"].startswith("2026-10-14")
    assert STORED not in body.text, "connector secrets are never returned"
    with SessionLocal() as db:
        row = db.query(ConnectorIntegration).filter(ConnectorIntegration.provider == "nvd").one()
        row.settings = {"sync": {"last_status": "failed"}}
        db.commit()
    assert client.get("/api/ui/v1/integrations").json()["nvd"]["state"] == "error"
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter(ConnectorIntegration.provider == "nvd").one().is_enabled = False
        db.commit()
    assert client.get("/api/ui/v1/integrations").json()["nvd"]["state"] == "disabled"
    assert client.get("/app/integrations").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    operator = login(f"ci-ui-int-op-{suffix}")
    seen = operator.get("/api/ui/v1/integrations")
    assert seen.status_code == 200 and seen.json()["can_admin"] is False
    assert TestClient(app).get("/api/ui/v1/integrations").status_code == 401
    print("ui integrations smoke ok")


if __name__ == "__main__":
    main()
