"""New interface, syslog administration API: settings (key-only mode always on), discarded senders, configure-all, firewall script."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import syslog_receiver
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, User
from app.security import hash_password
from app.syslog_models import SyslogUnknownSource

PASSWORD = "CI-UI-Syslog-Admin-2026-Pass!"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add_all([
            User(username=f"ci-usl-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-usl-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
            SyslogUnknownSource(source_ip="192.0.2.77", messages=12, sample="TEST line without key"),
        ])
        db.commit()

    client, headers = login(f"ci-usl-{suffix}")
    data = client.get("/api/ui/v1/admin/syslog").json()
    assert set(data) >= {"settings", "host", "target", "receiver", "reject_reasons", "stored_today", "senders_today", "unknown_sources", "firewall_script"}
    assert "missing_key" in data["reject_reasons"] and data["host"] == "testserver"
    assert any(row["source_ip"] == "192.0.2.77" and row["messages"] == 12 for row in data["unknown_sources"])

    assert client.post("/api/ui/v1/admin/syslog/settings", json={}).status_code == 403, "CSRF header required"
    bad = client.post("/api/ui/v1/admin/syslog/settings", headers=headers, json={"allowed_networks": "203.0.113.0/24\nnot-a-net"})
    assert bad.status_code == 422 and bad.json()["detail"] == {"code": "invalid_networks", "message": "not-a-net"}
    saved = client.post("/api/ui/v1/admin/syslog/settings", headers=headers, json={
        "public_host": "198.51.100.20", "info_retention_days": 30, "allowed_networks": "203.0.113.0/24, 198.51.100.0/22", "auto_configure": True})
    assert saved.status_code == 200, saved.text
    settings = saved.json()["settings"]
    assert settings["allowed_networks"] == ["203.0.113.0/24", "198.51.100.0/22"] and settings["strict_mode"] is True and settings["auto_configure"] is True
    with SessionLocal() as db:
        assert syslog_receiver.load_settings(db)["public_host"] == "198.51.100.20"
    after = client.get("/api/ui/v1/admin/syslog").json()
    assert after["target"] == "198.51.100.20" and "203.0.113.0/24" in after["firewall_script"]

    configured = client.post("/api/ui/v1/admin/syslog/configure-all", headers=headers)
    assert configured.status_code == 200 and configured.json()["target"] == "198.51.100.20", configured.text
    assert client.post("/api/ui/v1/admin/syslog/unknown/clear", headers=headers, json={"source_ip": "192.0.2.77"}).json() == {"cleared": True}
    with SessionLocal() as db:
        assert db.get(SyslogUnknownSource, "192.0.2.77") is None
        assert db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.event_type == "SYSLOG_SETTINGS_CHANGED"))
    assert client.get("/app/admin/syslog").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    tech, tech_headers = login(f"ci-usl-tech-{suffix}")
    assert tech.get("/api/ui/v1/admin/syslog").status_code == 403
    assert tech.post("/api/ui/v1/admin/syslog/configure-all", headers=tech_headers).status_code == 403
    print("ui syslog admin smoke ok")


if __name__ == "__main__":
    main()
