"""New interface, notification administration API: SMTP settings (secret never returned), test, Telegram validation, log, requeue."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import notification_delivery
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, User
from app.notification_models import NotificationDelivery
from app.security import hash_password

PASSWORD = "CI-UI-Notifications-2026-Pass!"
SMTP_SECRET = "ci-smtp-placeholder-value"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        admin = User(username=f"ci-un-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True, email=f"ci-un-{suffix}@example.test")
        db.add_all([admin, User(username=f"ci-un-tech-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)])
        db.flush()
        failed = NotificationDelivery(user_id=admin.id, channel="email", destination=f"ci-un-{suffix}@example.test", category="backup", severity="warning",
                                      subject=f"TEST failed {suffix}", body="TEST", status="failed", attempts=6, last_error="TEST unreachable")
        db.add(failed)
        db.commit()
        failed_id = str(failed.id)

    client, headers = login(f"ci-un-{suffix}")
    data = client.get("/api/ui/v1/admin/notifications").json()
    assert data["security_modes"] == ["starttls", "ssl", "none"] and data["smtp"]["configured"] is False
    assert any(row["id"] == failed_id and row["user"] == f"ci-un-{suffix}" for row in data["deliveries"])
    only_failed = client.get("/api/ui/v1/admin/notifications?status=failed").json()
    assert only_failed["status"] == "failed" and all(row["status"] == "failed" for row in only_failed["deliveries"])

    assert client.post("/api/ui/v1/admin/notifications/smtp", json={"host": "x"}).status_code == 403, "CSRF header required"
    bad = client.post("/api/ui/v1/admin/notifications/smtp", headers=headers, json={"host": "smtp.example.test", "port": 99999, "from_address": "nsm@example.test"})
    assert bad.status_code == 400 and "porta" in bad.json()["detail"]
    saved = client.post("/api/ui/v1/admin/notifications/smtp", headers=headers, json={
        "host": "smtp.example.test", "port": 587, "security": "starttls", "username": "nsm", "password": SMTP_SECRET,
        "from_address": "nsm@example.test", "from_name": "NSM CI", "portal_url": "https://nsm.example.test/", "enabled": False})
    assert saved.json() == {"saved": True}, saved.text
    smtp = client.get("/api/ui/v1/admin/notifications").json()["smtp"]
    assert smtp["configured"] is True and smtp["enabled"] is False and smtp["host"] == "smtp.example.test" and smtp["portal_url"] == "https://nsm.example.test"
    listing = client.get("/api/ui/v1/admin/notifications").text
    assert SMTP_SECRET not in listing, "stored secret never returned"
    with SessionLocal() as db:
        row = notification_delivery.smtp_row(db)
        assert row.secret_encrypted != SMTP_SECRET
        before = row.secret_encrypted
    assert client.post("/api/ui/v1/admin/notifications/smtp", headers=headers, json={
        "host": "smtp.example.test", "port": 465, "security": "ssl", "from_address": "nsm@example.test", "enabled": False}).status_code == 200
    with SessionLocal() as db:
        assert notification_delivery.smtp_row(db).secret_encrypted == before, "empty password keeps the stored one"

    tested = client.post("/api/ui/v1/admin/notifications/smtp/test", headers=headers, json={}).json()
    assert tested["result"] == "failed" and "disabilitato" in tested["message"], tested
    assert client.get("/api/ui/v1/admin/notifications").json()["smtp"]["last_test_status"] == "failed"

    malformed = client.post("/api/ui/v1/admin/notifications/telegram", headers=headers, json={"token": "not-a-token", "enabled": True})
    assert malformed.status_code == 400
    assert client.post("/api/ui/v1/admin/notifications/telegram", headers=headers, json={"token": "", "enabled": True}).status_code == 400

    assert client.post("/api/ui/v1/admin/notifications/retry", headers=headers, json={"delivery": "nope"}).status_code == 400
    assert client.post("/api/ui/v1/admin/notifications/retry", headers=headers, json={"delivery": failed_id}).json() == {"requeued": 1}
    with SessionLocal() as db:
        delivery = db.get(NotificationDelivery, uuid.UUID(failed_id))
        assert delivery.status == "pending" and delivery.attempts == 0
        kinds = dict(db.execute(select(AuditEvent.event_type, func.count(AuditEvent.id)).where(AuditEvent.event_type.like("NOTIFICATION_%"))
                                .group_by(AuditEvent.event_type)).all())
    for kind in ("NOTIFICATION_SMTP_CONFIGURED", "NOTIFICATION_SMTP_TESTED", "NOTIFICATION_DELIVERIES_REQUEUED"):
        assert kinds.get(kind), f"{kind} audited"
    assert client.get("/app/admin/notifications").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    tech, tech_headers = login(f"ci-un-tech-{suffix}")
    assert tech.get("/api/ui/v1/admin/notifications").status_code == 403
    assert tech.post("/api/ui/v1/admin/notifications/retry", headers=tech_headers, json={}).status_code == 403
    print("ui notifications admin smoke ok")


if __name__ == "__main__":
    main()
