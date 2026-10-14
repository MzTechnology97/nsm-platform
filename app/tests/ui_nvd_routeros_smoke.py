"""New interface, NVD source and RouterOS catalogue APIs: settings (key never returned), guards, matching, catalogue view."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app import advisory_sources
from app.db import SessionLocal
from app.entrypoint import app
from app.models import RouterosRelease, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Nvd-Routeros-2026-Pass!"
KEY = "ci-nvd-placeholder-key"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        db.add_all([
            User(username=f"ci-unr-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-unr-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
            RouterosRelease(channel="stable", version="7.99.1", released_at=now - timedelta(days=2), changelog="*) ssh - fixed CVE-2099-0001", security=True,
                            security_lines=["*) ssh - fixed CVE-2099-0001"]),
            RouterosRelease(channel="stable", version="7.99.0", released_at=now - timedelta(days=20), changelog="*) misc fixes", security=False, security_lines=[]),
        ])
        db.commit()

    client, headers = login(f"ci-unr-{suffix}")
    nvd = client.get("/api/ui/v1/admin/integrations/nvd").json()
    assert {"configured", "enabled", "has_key", "interval", "sync", "matching", "advisory_count", "open_findings", "tracked_cpes", "coverage"} <= set(nvd)
    assert nvd["interval_min"] <= nvd["interval"] <= nvd["interval_max"]

    assert client.post("/api/ui/v1/admin/integrations/nvd", json={}).status_code == 403, "CSRF header required"
    bad = client.post("/api/ui/v1/admin/integrations/nvd", headers=headers, json={"enabled": False, "interval_minutes": 1})
    assert bad.status_code == 400 and "intervallo" in bad.json()["detail"].lower()
    saved = client.post("/api/ui/v1/admin/integrations/nvd", headers=headers, json={"enabled": False, "interval_minutes": 360, "api_key": KEY})
    assert saved.json() == {"saved": True}, saved.text
    body = client.get("/api/ui/v1/admin/integrations/nvd")
    assert body.json()["has_key"] is True and body.json()["enabled"] is False and KEY not in body.text, "stored key never returned"
    with SessionLocal() as db:
        assert advisory_sources.connection_for(db).secret_encrypted not in ("", KEY)
    disabled = client.post("/api/ui/v1/admin/integrations/nvd/sync", headers=headers, json={})
    assert disabled.status_code == 400 and "Abilita" in disabled.json()["detail"]
    matched = client.post("/api/ui/v1/admin/integrations/nvd/match", headers=headers)
    assert matched.status_code == 200 and "resolved" in matched.json(), matched.text
    assert client.post("/api/ui/v1/admin/integrations/nvd", headers=headers, json={"enabled": False, "interval_minutes": 360, "clear_api_key": True}).status_code == 200
    assert client.get("/api/ui/v1/admin/integrations/nvd").json()["has_key"] is False

    catalog = client.get("/api/ui/v1/integrations/routeros").json()
    assert catalog["can_refresh"] is True and "stable" in catalog["channels"]
    assert catalog["heads"]["stable"]["version"] == "7.99.1" and catalog["heads"]["stable"]["security"] is True
    assert [r["version"] for r in catalog["history"] if r["version"].startswith("7.99.")] == ["7.99.1", "7.99.0"]
    for path in ("/app/admin/integrations/nvd", "/app/integrations/routeros"):
        assert client.get(path).status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    tech, tech_headers = login(f"ci-unr-tech-{suffix}")
    assert tech.get("/api/ui/v1/admin/integrations/nvd").status_code == 403
    seen = tech.get("/api/ui/v1/integrations/routeros").json()
    assert seen["can_refresh"] is False and seen["heads"]["stable"]["version"] == "7.99.1"
    assert tech.post("/api/ui/v1/integrations/routeros/refresh", headers=tech_headers).status_code == 403
    print("ui nvd routeros smoke ok")


if __name__ == "__main__":
    main()
