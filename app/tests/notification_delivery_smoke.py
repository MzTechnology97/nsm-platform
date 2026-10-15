"""External notifications: SMTP configuration, per-user preferences, fan-out, retries and delivery log."""
import re
import smtplib
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import notification_delivery as nd
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Notification, User, utcnow
from app.notification_models import NotificationDelivery, UserNotificationPreference
from app.secret_vault import decrypt_text
from app.security import hash_password

PASSWORD = "CI-Notification-Delivery-2026"
SENT = []
STATE = {"fail": False}


class FakeSMTP:
    def __init__(self, host, port, timeout=None, **kwargs):
        if STATE["fail"]:
            raise smtplib.SMTPConnectError(421, "server busy")
        self.host, self.port, self.logged = host, port, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        self.logged = (user, password)

    def send_message(self, message):
        SENT.append({"to": message["To"], "subject": message["Subject"], "body": message.get_content(), "login": self.logged})


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(username):
    client = TestClient(app)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    return client


def main():
    nd.smtplib.SMTP = FakeSMTP
    suffix = uuid.uuid4().hex[:6]
    names = {k: f"ci-nd-{k}-{suffix}" for k in ("admin", "backup", "all", "off", "tech")}
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter(ConnectorIntegration.provider == "smtp").delete()
        for key, name in names.items():
            db.add(User(username=name, password_hash=hash_password(PASSWORD), role="technician" if key == "tech" else "admin", is_active=True,
                        email=None if key == "tech" else f"{key}-{suffix}@example.test"))
        db.commit()

    admin = login(names["admin"])
    admin_api = "/api/ui/v1/admin/notifications"
    admin_headers = {"X-CSRF-Token": admin.get("/api/ui/v1/session").json()["csrf_token"]}
    assert admin.get(admin_api).json()["smtp"]["configured"] is False
    saved = admin.post(f"{admin_api}/smtp", headers=admin_headers, json={"host": "smtp.example.test", "port": 587, "security": "starttls",
                       "username": "nsm", "password": "smtp-secret-ci", "from_address": "nsm@example.test", "from_name": "NSM CI",
                       "portal_url": "https://nsm.example.test", "enabled": True})
    assert saved.status_code == 200 and "smtp-secret-ci" not in saved.text, saved.text
    assert admin.get(admin_api).json()["smtp"]["has_password"] is True
    with SessionLocal() as db:
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "smtp"))
        assert decrypt_text(row.secret_encrypted) == "smtp-secret-ci" and row.settings["host"] == "smtp.example.test"
    tested = admin.post(f"{admin_api}/smtp/test", headers=admin_headers, json={}).json()
    assert "E-mail di prova inviata" in tested["message"] and SENT[-1]["to"] == f"admin-{suffix}@example.test" and SENT[-1]["login"] == ("nsm", "smtp-secret-ci")

    # Preferences from the profile page.
    def save(client, body):
        return client.post("/api/ui/v1/profile/notifications", headers={"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}, json=body)

    for key, channel in (("backup", {"enabled": True, "level": "high", "categories": ["backup"]}),
                         ("all", {"enabled": True, "level": "info"}),
                         ("off", {"level": "info"})):
        client = login(names[key])
        assert "email" in client.get("/api/ui/v1/profile").json()["notifications"]["channels"]
        response = save(client, {"email": f"{key}-{suffix}@example.test", "channels": {"email": channel}})
        assert response.json() == {"ok": True}, response.text
    tech = login(names["tech"])
    bad = save(tech, {"email": "", "channels": {"email": {"enabled": True}}})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "notification_email", "e-mail channel needs an address"

    SENT.clear()
    with SessionLocal() as db:
        db.add(Notification(severity="critical", category="backup", title=f"Backup fallito {suffix}", message="Tre tentativi falliti.", source_url="/operations/backups"))
        db.add(Notification(severity="warning", category="integration", title=f"UISP lento {suffix}", message="Risposta lenta."))
        db.commit()
        rows = list(db.scalars(select(NotificationDelivery).where(NotificationDelivery.subject.like(f"%{suffix}%"))))
        recipients = sorted((r.destination, r.category) for r in rows)
        assert recipients == sorted([(f"backup-{suffix}@example.test", "backup"), (f"all-{suffix}@example.test", "backup"), (f"all-{suffix}@example.test", "integration")]), recipients
    stats = nd.deliver_pending()
    assert stats["sent"] >= 3
    mail = next(m for m in SENT if m["to"] == f"backup-{suffix}@example.test")
    assert mail["subject"].startswith("[NSM] Critico") and "https://nsm.example.test/operations/backups" in mail["body"]

    # Retries with backoff, then failed and visible, then re-queued.
    STATE["fail"] = True
    with SessionLocal() as db:
        db.add(Notification(severity="critical", category="system", title=f"Worker fermo {suffix}"))
        db.commit()
    now = utcnow()
    for attempt in range(nd.MAX_ATTEMPTS):
        nd.deliver_pending(now + timedelta(days=attempt + 1))
    with SessionLocal() as db:
        failed = db.scalar(select(NotificationDelivery).where(NotificationDelivery.subject.like(f"%Worker fermo {suffix}%")))
        assert failed.status == "failed" and failed.attempts == nd.MAX_ATTEMPTS and "SMTPConnectError" in failed.last_error
    log = admin.get(admin_api, params={"status": "failed"}).json()
    assert any(f"Worker fermo {suffix}" in (d["subject"] or "") for d in log["deliveries"])
    connectors = {row["name"]: row for row in admin.get("/api/ui/v1/admin/system").json()["connectors"]}
    assert connectors["Notifiche e-mail"]["state"] == "error", connectors["Notifiche e-mail"]
    STATE["fail"] = False
    admin.post(f"{admin_api}/retry", headers=admin_headers, json={}).raise_for_status()
    nd.deliver_pending()
    with SessionLocal() as db:
        assert db.get(NotificationDelivery, failed.id).status == "sent", "a re-queued delivery is sent once the server is back"

    # Test message from the profile.
    SENT.clear()
    client = login(names["off"])
    test_url = "/api/ui/v1/profile/notifications/test"
    response = client.post(test_url, headers={"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]})
    assert response.json()["queued"] is False, "disabled channels receive nothing"
    client = login(names["all"])
    response = client.post(test_url, headers={"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]})
    assert response.json()["queued"] is True and SENT and SENT[-1]["to"] == f"all-{suffix}@example.test"

    assert tech.get("/api/ui/v1/admin/notifications").status_code in (401, 403)
    with SessionLocal() as db:
        assert db.scalar(select(UserNotificationPreference).where(UserNotificationPreference.channel == "email", UserNotificationPreference.enabled.is_(True)))
    print("Notification delivery smoke passed")


if __name__ == "__main__":
    main()
