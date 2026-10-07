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
    page = admin.get("/admin/notifications").text
    assert 'data-notifications="smtp"' in page and 'href="/admin/notifications"' in page
    saved = admin.post("/admin/notifications/smtp", data={"csrf": csrf_from(page), "host": "smtp.example.test", "port": "587", "security": "starttls",
                       "username": "nsm", "password": "smtp-secret-ci", "from_address": "nsm@example.test", "from_name": "NSM CI",
                       "portal_url": "https://nsm.example.test", "is_enabled": "on"}, follow_redirects=True)
    assert "Configurazione SMTP salvata" in saved.text and "smtp-secret-ci" not in saved.text
    with SessionLocal() as db:
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "smtp"))
        assert decrypt_text(row.secret_encrypted) == "smtp-secret-ci" and row.settings["host"] == "smtp.example.test"
    tested = admin.post("/admin/notifications/smtp/test", data={"csrf": csrf_from(page)}, follow_redirects=True)
    assert "E-mail di prova inviata" in tested.text and SENT[-1]["to"] == f"admin-{suffix}@example.test" and SENT[-1]["login"] == ("nsm", "smtp-secret-ci")

    # Preferences from the profile page.
    for key, data in (("backup", {"email_enabled": "on", "email_level": "high", "email_categories": ["backup"]}),
                      ("all", {"email_enabled": "on", "email_level": "info"}),
                      ("off", {"email_level": "info"})):
        client = login(names[key])
        profile = client.get("/profile").text
        assert 'data-profile="notifications"' in profile
        response = client.post("/profile/notifications", data={"csrf": csrf_from(profile), "email": f"{key}-{suffix}@example.test", **data}, follow_redirects=False)
        assert "notifications_saved" in response.headers["location"], response.headers["location"]
    tech = login(names["tech"])
    bad = tech.post("/profile/notifications", data={"csrf": csrf_from(tech.get("/profile").text), "email": "", "email_enabled": "on"}, follow_redirects=False)
    assert "error=notification_email" in bad.headers["location"], "e-mail channel needs an address"

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
    log = admin.get("/admin/notifications?status=failed").text
    assert f"Worker fermo {suffix}" in log and "Rimetti in coda i falliti" in log
    assert 'data-connector="Notifiche e-mail" data-state="error"' in admin.get("/admin/system").text
    STATE["fail"] = False
    admin.post("/admin/notifications/retry", data={"csrf": csrf_from(log)})
    nd.deliver_pending()
    with SessionLocal() as db:
        assert db.get(NotificationDelivery, failed.id).status == "sent", "a re-queued delivery is sent once the server is back"

    # Test message from the profile.
    SENT.clear()
    client = login(names["off"])
    response = client.post("/profile/notifications/test", data={"csrf": csrf_from(client.get("/profile").text)}, follow_redirects=False)
    assert "notifications_test_none" in response.headers["location"], "disabled channels receive nothing"
    client = login(names["all"])
    response = client.post("/profile/notifications/test", data={"csrf": csrf_from(client.get("/profile").text)}, follow_redirects=False)
    assert "notifications_test_queued" in response.headers["location"] and SENT and SENT[-1]["to"] == f"all-{suffix}@example.test"

    assert tech.get("/admin/notifications", follow_redirects=False).status_code in (401, 403)
    with SessionLocal() as db:
        assert db.scalar(select(UserNotificationPreference).where(UserNotificationPreference.channel == "email", UserNotificationPreference.enabled.is_(True)))
    print("Notification delivery smoke passed")


if __name__ == "__main__":
    main()
