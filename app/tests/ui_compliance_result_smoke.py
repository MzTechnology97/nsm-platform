"""New interface, compliance result: detail, acknowledge, exception (coded checks), revoke, Action Center sync, history, permissions."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.compliance_engine import evaluate_all
from app.compliance_findings import ISSUE_TITLE, housekeeping
from app.compliance_models import ComplianceBaseline, ComplianceResult
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Compliance-Result-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def code(response):
    assert response.status_code == 422, response.text
    return response.json()["detail"]["code"]


def open_issue(db, device_id):
    return db.query(ActionIssue).filter(ActionIssue.title == ISSUE_TITLE, ActionIssue.device_id == device_id, ActionIssue.status.in_(["open", "acknowledged"])).one_or_none()


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        db.query(ComplianceBaseline).delete()
        customer = Customer(name=f"CI UI Compliance {suffix}", code=f"UC{suffix[:6]}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-UC-RTR-{suffix}", status="online",
                        firmware_status="current", lifecycle_status="supported")
        db.add(device)
        db.add_all([
            User(username=f"ci-uc-{suffix}", display_name="CI Compliance Tech", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
            User(username=f"ci-uc-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.add(ComplianceBaseline(
            name=f"TEST UC {suffix}", scope_type="customer", customer_id=customer.id, is_enabled=True, version=1,
            controls={"backup_recent": {"enabled": True, "params": {"max_age_days": 7}}, "firmware_current": {"enabled": True, "params": {"strict": False}}},
            created_at=now, updated_at=now,
        ))
        db.flush()
        evaluate_all(db, now, [device.id])
        db.flush()
        housekeeping(db, now)
        db.commit()
        failing = db.query(ComplianceResult).filter_by(device_id=device.id, control_id="backup_recent").one()
        passing = db.query(ComplianceResult).filter_by(device_id=device.id, control_id="firmware_current").one()
        assert failing.status == "fail" and passing.status == "pass"
        ids = {"device": device.id, "fail": str(failing.id), "pass": str(passing.id)}
        assert open_issue(db, device.id) is not None

    base = f"/api/ui/v1/compliance/results/{ids['fail']}"
    assert TestClient(app).get(base).status_code == 401
    tech, csrf = login(f"ci-uc-{suffix}")
    auditor, auditor_csrf = login(f"ci-uc-aud-{suffix}")

    detail = auditor.get(base).json()
    assert detail["status"] == "fail" and detail["control_id"] == "backup_recent" and detail["control"]["title"]
    assert detail["device"]["brand"]["symbol"] == "brand-mikrotik" and detail["customer"]["name"].startswith("CI UI Compliance")
    assert detail["baseline"]["version"] == 1 and detail["acknowledged"] is None and detail["exception"] is None and detail["can_manage"] is False
    assert auditor.get(f"/api/ui/v1/compliance/results/{uuid.uuid4()}").status_code == 404

    assert auditor.post(f"{base}/acknowledge", headers=auditor_csrf, json={"note": "x"}).status_code == 403
    assert tech.post(f"{base}/acknowledge", json={"note": "x"}).status_code == 403, "CSRF header required"
    assert code(tech.post(f"{base}/explode", headers=csrf, json={})) == "action"
    assert tech.post(f"{base}/acknowledge", headers=csrf, json={"note": "TEST planned with the customer"}).json()["message"]
    detail = tech.get(base).json()
    assert detail["acknowledged"]["by"] == "CI Compliance Tech" and detail["can_manage"] is True

    today = now.date()
    assert code(tech.post(f"{base}/exception", headers=csrf, json={"reason": " ", "until": (today + timedelta(days=30)).isoformat()})) == "reason"
    assert code(tech.post(f"{base}/exception", headers=csrf, json={"reason": "TEST lab", "until": "soon"})) == "until"
    assert code(tech.post(f"{base}/exception", headers=csrf, json={"reason": "TEST lab", "until": (today + timedelta(days=400)).isoformat()})) == "until_range"
    assert code(tech.post(f"{base}/revoke", headers=csrf, json={})) == "no_exception"
    tech.post(f"{base}/exception", headers=csrf, json={"reason": "TEST isolated lab device", "until": (today + timedelta(days=30)).isoformat()}).raise_for_status()
    detail = tech.get(base).json()
    assert detail["exception"]["reason"] == "TEST isolated lab device"
    with SessionLocal() as db:
        assert open_issue(db, ids["device"]) is None, "an exception removes the Action Center issue"

    tech.post(f"{base}/revoke", headers=csrf, json={"note": "TEST revoked"}).raise_for_status()
    detail = tech.get(base).json()
    assert detail["exception"] is None
    assert [h["action"] for h in detail["history"]][:3] == ["exception_revoked", "exception", "acknowledged"]
    assert detail["history"][0]["by"] == "CI Compliance Tech" and detail["history"][0]["note"] == "TEST revoked"
    with SessionLocal() as db:
        assert open_issue(db, ids["device"]) is not None
        events = {e.event_type for e in db.query(AuditEvent).filter(AuditEvent.device_id == ids["device"])}
        assert {"COMPLIANCE_ACKNOWLEDGED", "COMPLIANCE_EXCEPTION", "COMPLIANCE_EXCEPTION_REVOKED"} <= events

    assert code(tech.post(f"/api/ui/v1/compliance/results/{ids['pass']}/acknowledge", headers=csrf, json={})) == "not_fail"

    assert tech.get(f"/app/compliance/results/{ids['fail']}").status_code in (200, 503)
    print("ui compliance result smoke ok")


if __name__ == "__main__":
    main()
