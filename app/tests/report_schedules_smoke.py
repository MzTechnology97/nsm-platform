"""REP-03: scheduled report generation for closed periods."""
import re
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

import app.report_schedules as schedules_module
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, Customer, Device, User
from app.report_models import GeneratedReport, ReportSchedule
from app.report_schedules import last_closed_period, run_report_schedules
from app.security import hash_password

PASSWORD = "CI-Report-Schedules-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def check_periods():
    assert last_closed_period("monthly", date(2026, 10, 7)) == (date(2026, 9, 1), date(2026, 9, 30))
    assert last_closed_period("monthly", date(2026, 1, 1)) == (date(2025, 12, 1), date(2025, 12, 31))
    assert last_closed_period("quarterly", date(2026, 10, 7)) == (date(2026, 7, 1), date(2026, 9, 30))
    assert last_closed_period("quarterly", date(2026, 2, 15)) == (date(2025, 10, 1), date(2025, 12, 31))
    assert last_closed_period("annual", date(2026, 3, 1)) == (date(2025, 1, 1), date(2025, 12, 31))


def seed():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        user = User(username=f"ci-sched-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        customer = Customer(name=f"CI Schedule {suffix}", code=f"SC{suffix[:6]}")
        db.add_all([user, customer])
        db.flush()
        db.add(Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-SCHED-RTR"))
        db.commit()
        return user.username, customer.id


def schedule_by_name(name):
    with SessionLocal() as db:
        return db.scalar(select(ReportSchedule).where(ReportSchedule.name == name))


def reports_for(schedule_id):
    with SessionLocal() as db:
        return list(db.scalars(select(GeneratedReport).where(GeneratedReport.schedule_id == schedule_id)))


def main():
    check_periods()
    username, customer_id = seed()
    client = TestClient(app)
    token = csrf_from(client.get("/login").text)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303

    page = client.get("/audit/reports")
    assert "Pianificazioni" in page.text and "Aggiungi pianificazione" in page.text
    invalid = client.post(
        "/audit/reports/schedules",
        data={"csrf": csrf_from(page.text), "name": " ", "frequency": "monthly", "output_format": "pdf"},
        follow_redirects=False,
    )
    assert "Indica un nome" in client.get(invalid.headers["location"]).text
    created = client.post(
        "/audit/reports/schedules",
        data={
            "csrf": csrf_from(page.text),
            "name": "TEST mensile cliente",
            "customer_id": str(customer_id),
            "frequency": "monthly",
            "output_format": "csv",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    schedule = schedule_by_name("TEST mensile cliente")
    assert schedule and schedule.is_enabled and schedule.customer_id == customer_id

    # First tick generates the last closed month exactly once.
    now = datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc)
    stats = run_report_schedules(now)
    assert stats["generated"] >= 1
    run_report_schedules(now + timedelta(minutes=1))
    generated = reports_for(schedule.id)
    assert len(generated) == 1, "a closed period is generated once"
    report = generated[0]
    assert (report.period_start, report.period_end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert report.customer_id == customer_id and report.output_format == "csv"
    assert report.generated_by_user_id is None and report.summary["trigger"] == "schedule"
    assert "TEST-SCHED-RTR" in report.content.decode("utf-8")
    assert schedule_by_name("TEST mensile cliente").last_period_end == date(2026, 9, 30)

    # Next month closes: a new report for October.
    run_report_schedules(datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc))
    periods = sorted(r.period_end for r in reports_for(schedule.id))
    assert periods == [date(2026, 9, 30), date(2026, 10, 31)]

    # Failures back off, are audited and raise one Action Center issue after 3 attempts.
    original = schedules_module.create_report

    def broken(*args, **kwargs):
        raise RuntimeError("TEST renderer failure")

    schedules_module.create_report = broken
    try:
        t = datetime(2026, 12, 3, 8, 0, tzinfo=timezone.utc)
        for expected in (1, 2, 3):
            run_report_schedules(t)
            current = schedule_by_name("TEST mensile cliente")
            assert current.last_status == "failed" and current.consecutive_failures == expected
            assert "TEST renderer failure" in current.last_error
            assert run_report_schedules(current.next_attempt_at - timedelta(seconds=1))["failed"] == 0, "backoff respected"
            t = current.next_attempt_at
        with SessionLocal() as db:
            issues = [
                i
                for i in db.scalars(select(ActionIssue).where(ActionIssue.title == "Report pianificato non generato", ActionIssue.status == "open"))
                if i.details.get("schedule_id") == str(schedule.id)
            ]
            assert len(issues) == 1
    finally:
        schedules_module.create_report = original

    run_report_schedules(t)
    current = schedule_by_name("TEST mensile cliente")
    assert current.last_status == "success" and current.consecutive_failures == 0
    assert current.last_period_end == date(2026, 11, 30)
    with SessionLocal() as db:
        still_open = [
            i
            for i in db.scalars(select(ActionIssue).where(ActionIssue.title == "Report pianificato non generato", ActionIssue.status == "open"))
            if i.details.get("schedule_id") == str(schedule.id)
        ]
        assert still_open == []
        events = {e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("REPORT_SCHEDULE_%")))}
        assert {"REPORT_SCHEDULE_CREATED", "REPORT_SCHEDULE_FAILED"} <= events

    # Suspend, page shows the scheduled report, delete keeps archived reports.
    page = client.get("/audit/reports")
    assert "pianificato" in page.text and "TEST mensile cliente" in page.text
    client.post(f"/audit/reports/schedules/{schedule.id}/toggle", data={"csrf": csrf_from(page.text)})
    assert schedule_by_name("TEST mensile cliente").is_enabled is False
    assert run_report_schedules(datetime(2027, 1, 5, 8, 0, tzinfo=timezone.utc))["generated"] == 0
    client.post(f"/audit/reports/schedules/{schedule.id}/delete", data={"csrf": csrf_from(page.text)})
    assert schedule_by_name("TEST mensile cliente") is None
    with SessionLocal() as db:
        kept = list(db.scalars(select(GeneratedReport).where(GeneratedReport.customer_id == customer_id)))
        assert len(kept) == 3 and all(r.schedule_id is None for r in kept)
    print("Report schedules smoke passed")


if __name__ == "__main__":
    main()
