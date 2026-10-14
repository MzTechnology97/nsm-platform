"""New interface, system status API: same data as the classic Sistema page, with translation keys, admin only."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import APP_VERSION, app
from app.models import User
from app.security import hash_password

PASSWORD = "CI-UI-System-2026-Pass!"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add_all([
            User(username=f"ci-us-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-us-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
        ])
        db.commit()

    client = login(f"ci-us-{suffix}")
    data = client.get("/api/ui/v1/admin/system").json()
    assert data["version"] == APP_VERSION and data["database"]["revision"], data["database"]
    assert set(data) >= {"worker", "connectors", "tables", "archive", "retention", "secrets", "dumps", "drills", "dump_max_days", "drill_max_days"}
    assert {"available", "alive", "tasks"} <= set(data["worker"])
    assert [row["key"] for row in data["retention"]] == ["mikrotik_telemetry", "uisp_metrics", "notifications", "login_failures", "backup_files", "evidence"]
    assert data["retention"][0]["days"] > 0 and data["retention"][-1]["days"] is None
    assert [row["key"] for row in data["secrets"]["rows"]] == [
        "connector_integrations.secret_encrypted", "mikrotik_backup_job_secrets.encrypted_backup_password",
        "users.totp_secret_encrypted", "user_notification_preferences.secret_encrypted"]
    assert all({"name", "state", "href"} <= set(connector) for connector in data["connectors"]) and data["connectors"]
    assert isinstance(data["dumps"]["stale"], bool) and isinstance(data["drills"]["overdue"], bool)
    assert client.get("/app/admin/system").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    assert login(f"ci-us-tech-{suffix}").get("/api/ui/v1/admin/system").status_code == 403
    print("ui system smoke ok")


if __name__ == "__main__":
    main()
