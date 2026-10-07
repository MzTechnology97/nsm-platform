"""Vulnerability newsletter, scheduled report delivery with attachment, worker error alerts."""
import hashlib
import re
import uuid
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import notification_delivery as nd
from app import worker_status
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, DeviceVulnerability, Notification, SecurityAdvisory, User, utcnow
from app.notification_digest import run_vulnerability_digest
from app.notification_models import NotificationDelivery, UserNotificationPreference
from app.report_models import GeneratedReport, ReportSchedule
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-Notification-Digest-2026"
SENT = []


class FakeSMTP:
    def __init__(self, host, port, timeout=None, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        attachments = [(part.get_filename(), part.get_content_type(), part.get_payload(decode=True)) for part in message.iter_attachments()]
        body = message.get_body(("plain",)).get_content()
        SENT.append({"to": message["To"], "subject": message["Subject"], "body": body, "attachments": attachments})


class FakeRedis:
    def __init__(self):
        self.values, self.hashes = {}, {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def boom():
    raise RuntimeError("database unreachable")


def main():
    nd.smtplib.SMTP = FakeSMTP
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "smtp"))
        db.add(ConnectorIntegration(provider="smtp", name="SMTP", base_url="smtp://smtp.example.test:587", secret_encrypted=encrypt_text("-"),
                                    is_enabled=True, settings={"host": "smtp.example.test", "port": 587, "security": "starttls", "from_address": "nsm@example.test",
                                                               "portal_url": "https://nsm.example.test"}))
        users = {}
        for key in ("digest", "report", "none"):
            users[key] = User(username=f"ci-dg-{key}-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True,
                              email=f"{key}-{suffix}@example.test")
            db.add(users[key])
        db.flush()
        users["digest"].notify_digest = "daily"
        db.add_all([UserNotificationPreference(user_id=users["digest"].id, channel="email", enabled=True, min_severity="critical", categories=["system"]),
                    UserNotificationPreference(user_id=users["report"].id, channel="email", enabled=True, min_severity="critical", categories=["reports"])])
        customer = Customer(name=f"CI Digest {suffix}", code=f"DG{suffix}")
        db.add(customer)
        db.flush()
        devices = [Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DG-{i}-{suffix}") for i in range(3)]
        db.add_all(devices)
        a1 = SecurityAdvisory(cve_id=f"CVE-2099-{uuid.uuid4().int % 90000 + 10000}", severity="critical", cvss=9.8, summary="Remote code execution in TEST service.")
        a2 = SecurityAdvisory(cve_id=f"CVE-2099-{uuid.uuid4().int % 90000 + 10000}", severity="medium", cvss=5.3)
        db.add_all([a1, a2])
        db.flush()
        for device in devices:
            db.add(DeviceVulnerability(advisory_id=a1.id, device_id=device.id, status="open", detected_at=now - timedelta(hours=2)))
        db.add(DeviceVulnerability(advisory_id=a2.id, device_id=devices[0].id, status="open", detected_at=now - timedelta(hours=3)))
        db.commit()
        ids = {k: u.id for k, u in users.items()}
        cve1 = a1.cve_id

    # Newsletter: new exposures with device counts, then nothing until due and nothing when there is nothing new.
    stats = run_vulnerability_digest(now)
    assert stats["sent"] >= 1
    with SessionLocal() as db:
        digest = db.scalar(select(NotificationDelivery).where(NotificationDelivery.user_id == ids["digest"], NotificationDelivery.subject.like("%Newsletter%")))
        assert "2 nuove CVE, 3 apparati coinvolti" in digest.subject and digest.severity == "critical"
        assert f"{cve1} (critical, CVSS 9.8) — 3 apparati esposti" in digest.body and "https://nsm.example.test/security/vulnerabilities" in digest.body
        assert not db.scalar(select(NotificationDelivery).where(NotificationDelivery.user_id == ids["none"])), "only users who chose the newsletter"
    assert run_vulnerability_digest(now + timedelta(hours=2))["sent"] == 0, "not due yet"
    assert run_vulnerability_digest(now + timedelta(days=1, minutes=1))["sent"] == 0, "nothing new: nothing sent"

    # Scheduled report: queued with the file attached for users subscribed to reports.
    with SessionLocal() as db:
        schedule = ReportSchedule(name=f"Mensile {suffix}", output_format="csv", frequency="monthly")
        db.add(schedule)
        db.flush()
        content = b"device,status\nTEST,ok\n"
        db.add(GeneratedReport(report_type="executive", title=f"Report CI {suffix}", scope_type="all", scope_label="Tutti i clienti",
                               period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), output_format="csv", filename=f"report-{suffix}.csv",
                               media_type="text/csv", content=content, size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(), summary={},
                               schedule_id=schedule.id))
        db.add(GeneratedReport(report_type="executive", title=f"Manuale {suffix}", scope_type="all", scope_label="Tutti", period_start=date(2026, 9, 1),
                               period_end=date(2026, 9, 30), output_format="csv", filename="m.csv", media_type="text/csv", content=content,
                               size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(), summary={}))
        db.commit()
        queued = list(db.scalars(select(NotificationDelivery).where(NotificationDelivery.category == "reports")))
        assert [(d.user_id, d.attachment_ref is not None) for d in queued] == [(ids["report"], True)], "only scheduled reports, only subscribers"
    SENT.clear()
    nd.deliver_pending()
    report_mail = next(m for m in SENT if m["to"] == f"report-{suffix}@example.test")
    assert report_mail["attachments"] == [(f"report-{suffix}.csv", "text/csv", b"device,status\nTEST,ok\n")]
    assert "/audit/reports/" in report_mail["body"]
    assert any(m["to"] == f"digest-{suffix}@example.test" and "Newsletter" in m["subject"] for m in SENT)

    # Worker errors: third consecutive failure notifies, recovery notifies again.
    worker_status.use_client(FakeRedis())
    for _ in range(3):
        worker_status.run_task(f"ci_task_{suffix}", boom)
    worker_status.run_task(f"ci_task_{suffix}", boom)
    worker_status.run_task(f"ci_task_{suffix}", lambda: {})
    with SessionLocal() as db:
        titles = [n.title for n in db.scalars(select(Notification).where(Notification.title.like(f"%ci_task_{suffix}%")))]
        assert sorted(titles) == sorted([f"Task del worker in errore: ci_task_{suffix}", f"Task del worker ripristinato: ci_task_{suffix}"]), titles
        alert = db.scalar(select(NotificationDelivery).where(NotificationDelivery.user_id == ids["digest"], NotificationDelivery.subject.like(f"%in errore: ci_task_{suffix}%")))
        assert alert is None, "the digest user only wants critical system messages; the alert is 'alto'"

    # Profile newsletter setting.
    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-dg-none-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    profile = client.get("/profile").text
    assert 'data-profile="digest"' in profile
    saved = client.post("/profile/notifications/digest", data={"csrf": csrf_from(profile), "digest": "weekly"}, follow_redirects=False)
    assert "digest_saved" in saved.headers["location"]
    with SessionLocal() as db:
        assert db.get(User, ids["none"]).notify_digest == "weekly"
    print("Notification digest smoke passed")


if __name__ == "__main__":
    main()
