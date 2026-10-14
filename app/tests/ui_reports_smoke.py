"""New interface, reports API: archive, generation and schedule actions shared with the classic forms, permissions."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, User, utcnow
from app.report_models import GeneratedReport, ReportSchedule
from app.security import hash_password

PASSWORD = "CI-UI-Reports-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Reports {suffix}", code=f"UR{suffix}")
        db.add(customer)
        db.add_all([
            User(username=f"ci-ur-{suffix}", display_name=f"CI Reports {suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-ur-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        customer_id = str(customer.id)

    client, headers = login(f"ci-ur-{suffix}")
    page = client.get("/api/ui/v1/reports").json()
    assert page["can_generate"] is True and page["formats"] == ["pdf", "csv"] and page["max_days"] == 366
    assert set(page["frequencies"]) == {"monthly", "quarterly", "annual"}
    today = utcnow().date()

    assert client.post("/api/ui/v1/reports/generate", json={"output_format": "csv"}).status_code == 403, "CSRF header required"
    future = client.post("/api/ui/v1/reports/generate", headers=headers, json={
        "period_start": today.isoformat(), "period_end": (today + timedelta(days=1)).isoformat(), "output_format": "csv"})
    assert future.status_code == 400 and "futuro" in future.json()["detail"]
    reversed_period = client.post("/api/ui/v1/reports/generate", headers=headers, json={
        "period_start": today.isoformat(), "period_end": (today - timedelta(days=2)).isoformat(), "output_format": "csv"})
    assert reversed_period.status_code == 400
    assert client.post("/api/ui/v1/reports/generate", headers=headers, json={
        "period_start": today.isoformat(), "period_end": today.isoformat(), "output_format": "docx"}).status_code == 400
    made = client.post("/api/ui/v1/reports/generate", headers=headers, json={
        "customer_id": customer_id, "period_start": (today - timedelta(days=6)).isoformat(), "period_end": today.isoformat(), "output_format": "csv"})
    assert made.status_code == 200, made.text
    report = made.json()
    assert len(report["sha256"]) == 64 and report["download_url"].endswith(f"/{report['id']}/download")

    listed = client.get("/api/ui/v1/reports").json()
    row = next(item for item in listed["items"] if item["id"] == report["id"])
    assert row["customer"] == {"id": customer_id, "name": f"CI UI Reports {suffix}"} and row["format"] == "csv"
    assert row["generated_by"] == f"CI Reports {suffix}" and row["scheduled"] is False and row["summary"]["trigger"] == "manual"
    download = client.get(report["download_url"])
    assert download.status_code == 200 and download.headers["X-Content-SHA256"] == report["sha256"]

    bad = client.post("/api/ui/v1/reports/schedules", headers=headers, json={"name": "TEST", "recipients": "not-an-address"})
    assert bad.status_code == 400 and "e-mail" in bad.json()["detail"]
    assert client.post("/api/ui/v1/reports/schedules", headers=headers, json={"name": " "}).status_code == 400
    created = client.post("/api/ui/v1/reports/schedules", headers=headers, json={
        "name": f"TEST monthly {suffix}", "customer_id": customer_id, "output_format": "pdf", "frequency": "quarterly",
        "recipients": "noc@example.test, NOC@EXAMPLE.TEST"})
    assert created.status_code == 200, created.text
    schedule_id = created.json()["id"]
    schedule = next(s for s in client.get("/api/ui/v1/reports").json()["schedules"] if s["id"] == schedule_id)
    assert schedule["enabled"] is True and schedule["frequency"] == "quarterly" and schedule["recipients"] == ["noc@example.test", "NOC@example.test"]

    base = f"/api/ui/v1/reports/schedules/{schedule_id}"
    assert client.post(f"{base}/toggle", headers=headers).json() == {"enabled": False}
    assert client.post(f"{base}/toggle", headers=headers).json() == {"enabled": True}
    assert client.post(f"{base}/recipients", headers=headers, json={"recipients": "ops@example.test"}).json() == {"recipients": ["ops@example.test"]}
    assert client.post(f"{base}/recipients", headers=headers, json={"recipients": ""}).json() == {"recipients": []}
    assert client.post(f"/api/ui/v1/reports/schedules/{uuid.uuid4()}/toggle", headers=headers).status_code == 404
    assert client.post(f"{base}/delete", headers=headers).json() == {"deleted": True}
    with SessionLocal() as db:
        assert db.get(ReportSchedule, uuid.UUID(schedule_id)) is None
        assert db.get(GeneratedReport, uuid.UUID(report["id"])) is not None, "archived reports stay"
        kinds = dict(db.execute(select(AuditEvent.event_type, func.count(AuditEvent.id)).where(AuditEvent.event_type.like("REPORT_%"))
                                .group_by(AuditEvent.event_type)).all())
    for kind in ("REPORT_GENERATED", "REPORT_DOWNLOADED", "REPORT_SCHEDULE_CREATED", "REPORT_SCHEDULE_DISABLED", "REPORT_SCHEDULE_ENABLED",
                 "REPORT_SCHEDULE_RECIPIENTS_CHANGED", "REPORT_SCHEDULE_DELETED"):
        assert kinds.get(kind), f"{kind} audited"
    assert client.get("/app/reports").status_code in (200, 503)  # 503 when the frontend is not built (CI integration)

    auditor, auditor_headers = login(f"ci-ur-au-{suffix}")
    seen = auditor.get("/api/ui/v1/reports").json()
    assert seen["can_generate"] is False and any(item["id"] == report["id"] for item in seen["items"])
    assert auditor.post("/api/ui/v1/reports/generate", headers=auditor_headers, json={}).status_code == 403
    assert auditor.post("/api/ui/v1/reports/schedules", headers=auditor_headers, json={"name": "TEST"}).status_code == 403
    print("ui reports smoke ok")


if __name__ == "__main__":
    main()
