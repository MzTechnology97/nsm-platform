"""REP-04: external e-mail recipients of scheduled reports, delivery through the outbox and audit evidence."""
import hashlib
import re
import uuid
from datetime import date

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import notification_delivery as nd
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import AuditEvent, User
from app.notification_models import NotificationDelivery
from app.report_models import GeneratedReport, ReportSchedule
from app.report_schedules import MAX_RECIPIENTS, parse_recipients
from app.secret_vault import encrypt_text
from app.security import hash_password
from tests.notification_digest_smoke import SENT, FakeSMTP

PASSWORD = "CI-Report-Recipients-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    assert parse_recipients("a@example.test, B@Example.TEST\nb@example.test; a@example.test") == ["a@example.test", "B@example.test", "b@example.test"]
    assert parse_recipients("  ") == []
    for bad in ("not-an-address", "x@localhost", "a@b@example.test", "<x@example.test>"):
        try:
            parse_recipients(bad)
            raise AssertionError(bad)
        except HTTPException as exc:
            assert exc.status_code == 400
    try:
        parse_recipients(",".join(f"u{i}@example.test" for i in range(MAX_RECIPIENTS + 1)))
        raise AssertionError("limit")
    except HTTPException:
        pass

    nd.smtplib.SMTP = FakeSMTP
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.execute(delete(ConnectorIntegration).where(ConnectorIntegration.provider == "smtp"))
        db.add(ConnectorIntegration(provider="smtp", name="SMTP", base_url="smtp://smtp.example.test:587", secret_encrypted=encrypt_text("-"),
                                    is_enabled=True, settings={"host": "smtp.example.test", "port": 587, "security": "starttls", "from_address": "nsm@example.test",
                                                               "from_name": "NSM"}))
        db.add(User(username=f"ci-rr-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-rr-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get("/audit/reports").text
    assert 'name="recipients"' in page
    token = csrf_from(page)
    client.post("/audit/reports/schedules", data={"csrf": token, "name": f"Bad {suffix}", "frequency": "monthly", "output_format": "csv", "recipients": "nope"})
    with SessionLocal() as db:
        assert db.scalar(select(ReportSchedule).where(ReportSchedule.name == f"Bad {suffix}")) is None, "invalid recipients refuse the schedule"
    client.post("/audit/reports/schedules", data={"csrf": token, "name": f"Direzione {suffix}", "frequency": "monthly", "output_format": "csv",
                                                  "recipients": f"cda-{suffix}@example.test, audit-{suffix}@example.test"})
    with SessionLocal() as db:
        schedule = db.scalar(select(ReportSchedule).where(ReportSchedule.name == f"Direzione {suffix}"))
        assert schedule.recipients == [f"cda-{suffix}@example.test", f"audit-{suffix}@example.test"]
        schedule_id = schedule.id
    page = client.get("/audit/reports").text
    assert "2 destinatari esterni" in page and f"cda-{suffix}@example.test" in page
    client.post(f"/audit/reports/schedules/{schedule_id}/recipients", data={"csrf": csrf_from(page), "recipients": f"cda-{suffix}@example.test"})
    with SessionLocal() as db:
        assert db.get(ReportSchedule, schedule_id).recipients == [f"cda-{suffix}@example.test"]
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "REPORT_SCHEDULE_RECIPIENTS_CHANGED")) is not None

        # A generated report of the schedule is queued for the external address with the attachment.
        content = b"device,status\nTEST,ok\n"
        report = GeneratedReport(report_type="executive", title=f"Report RR {suffix}", scope_type="all", scope_label="Tutti i clienti",
                                 period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), output_format="csv", filename=f"rr-{suffix}.csv",
                                 media_type="text/csv", content=content, size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(), summary={},
                                 schedule_id=schedule_id)
        db.add(report)
        db.commit()
        report_id = report.id
        external = db.scalars(select(NotificationDelivery).where(NotificationDelivery.destination == f"cda-{suffix}@example.test")).all()
        assert len(external) == 1 and external[0].user_id is None and external[0].attachment_ref == f"report:{report_id}"
    SENT.clear()
    nd.deliver_pending()
    mail = next(m for m in SENT if m["to"] == f"cda-{suffix}@example.test")
    assert mail["attachments"] == [(f"rr-{suffix}.csv", "text/csv", content)] and f"Direzione {suffix}" in mail["body"]
    with SessionLocal() as db:
        evidence = [e for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type == "REPORT_DELIVERED"))
                    if (e.details or {}).get("report_id") == str(report_id)]
        assert len(evidence) == 1 and evidence[0].details["external"] and evidence[0].details["destination"] == f"cda-{suffix}@example.test"

        # Final failure leaves evidence too.
        db.add(NotificationDelivery(user_id=None, channel="email", destination=f"down-{suffix}@example.test", category="reports", severity="info",
                                    subject="x", body="x", status="pending", attempts=nd.MAX_ATTEMPTS - 1, attachment_ref=f"report:{report_id}"))
        db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "smtp")).is_enabled = False
        db.commit()
    nd.deliver_pending()
    with SessionLocal() as db:
        failed = [e for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type == "REPORT_DELIVERY_FAILED"))
                  if (e.details or {}).get("destination") == f"down-{suffix}@example.test"]
        assert len(failed) == 1 and "SMTP" in failed[0].details["error"]
    print("Report recipients smoke passed")


if __name__ == "__main__":
    main()
