"""COMP-03: non-compliance handling, exceptions with expiry, history and Action Center."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.compliance_engine import evaluate_all
from app.compliance_findings import ISSUE_TITLE, housekeeping
from app.compliance_models import ComplianceBaseline, ComplianceResult, ComplianceResultHistory
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Compliance-Findings-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(username):
    client = TestClient(app)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    return client


def issue(db, device_id):
    return db.query(ActionIssue).filter(ActionIssue.title == ISSUE_TITLE, ActionIssue.device_id == device_id, ActionIssue.status.in_(["open", "acknowledged"])).one_or_none()


def result(db, device_id, control):
    return db.query(ComplianceResult).filter_by(device_id=device_id, control_id=control).one()


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        db.query(ComplianceBaseline).delete()
        tech = User(username=f"ci-cf-{suffix}", display_name="CI Compliance Tech", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        auditor = User(username=f"ci-cf-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)
        customer = Customer(name=f"CI CompFind {suffix}", code=f"CF{suffix[:6]}")
        db.add_all([tech, auditor, customer])
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-CF-RTR", status="online", firmware_status="current", lifecycle_status="supported")
        db.add(device)
        db.add(ComplianceBaseline(
            name="TEST CF", scope_type="customer", customer_id=customer.id, is_enabled=True, version=1,
            controls={"backup_recent": {"enabled": True, "params": {"max_age_days": 7}}, "agent_heartbeat": {"enabled": True, "params": {"max_minutes": 15}}, "firmware_current": {"enabled": True, "params": {"strict": False}}},
            created_at=now, updated_at=now,
        ))
        db.flush()
        evaluate_all(db, now, [device.id])
        db.flush()
        housekeeping(db, now)
        db.commit()
        ids = {"device": device.id, "backup": result(db, device.id, "backup_recent").id, "agent": result(db, device.id, "agent_heartbeat").id}
        assert result(db, device.id, "firmware_current").status == "pass"
        assert issue(db, device.id).details["count"] == 2
        assert db.query(ComplianceResultHistory).filter_by(result_id=ids["backup"]).count() == 1

    client = login(f"ci-cf-{suffix}")
    page = client.get(f"/compliance/results/{ids['backup']}").text
    assert "Backup non eseguibile" in page and "Prendi in carico" in page and "Registra eccezione" in page
    token = csrf_from(page)
    assert "presa in carico" in client.post(f"/compliance/results/{ids['backup']}/acknowledge", data={"csrf": token, "note": "Policy in arrivo"}).text

    # Exceptions: reason and expiry within a year are mandatory.
    base = f"/compliance/results/{ids['agent']}/exception"
    assert "motivazione" in client.post(base, data={"csrf": token, "reason": "", "until": (now + timedelta(days=30)).date().isoformat()}).text
    assert "entro un anno" in client.post(base, data={"csrf": token, "reason": "x", "until": (now + timedelta(days=500)).date().isoformat()}).text
    until = (now + timedelta(days=30)).date()
    assert "Eccezione registrata" in client.post(base, data={"csrf": token, "reason": "TEST router in dismissione", "until": until.isoformat()}).text

    with SessionLocal() as db:
        assert result(db, ids["device"], "agent_heartbeat").status == "fail", "the evaluated status is never overwritten"
        assert issue(db, ids["device"]).details["count"] == 1, "an excepted failure is not 'to handle'"
        assert result(db, ids["device"], "backup_recent").acknowledged_by_user_id is not None
    overview = client.get(f"/compliance?status=exception").text
    assert "TEST-CF-RTR" in overview and "In eccezione" in overview and "in eccezione" in overview
    fails = client.get(f"/compliance?status=fail").text
    assert "presa in carico" in fails

    auditor = login(f"ci-cf-aud-{suffix}")
    view = auditor.get(f"/compliance/results/{ids['backup']}").text
    assert "Registra eccezione" not in view and "Policy in arrivo" in view and "CI Compliance Tech" in view
    assert auditor.post(f"/compliance/results/{ids['backup']}/acknowledge", data={"csrf": csrf_from(view)}).status_code == 403

    with SessionLocal() as db:
        # Expiry brings the failure back to 'to handle'.
        later = result(db, ids["device"], "agent_heartbeat").exception_until + timedelta(hours=1)
        stats = housekeeping(db, later)
        db.commit()
        assert stats["exceptions_expired"] == 1
        assert result(db, ids["device"], "agent_heartbeat").exception_until is None
        assert issue(db, ids["device"]).details["count"] == 2
        # A successful backup clears handling and is recorded.
        db.add(BackupRun(device_id=ids["device"], status="success", backup_type="mikrotik_multi", started_at=later, completed_at=later))
        db.flush()
        evaluate_all(db, later + timedelta(minutes=1), [ids["device"]])
        db.flush()
        housekeeping(db, later + timedelta(minutes=1))
        db.commit()
        backup = result(db, ids["device"], "backup_recent")
        assert backup.status == "pass" and backup.acknowledged_at is None
        actions = [h.action for h in db.query(ComplianceResultHistory).filter_by(result_id=ids["backup"]).order_by(ComplianceResultHistory.created_at)]
        assert actions == ["evaluated", "acknowledged", "evaluated"], actions
        assert issue(db, ids["device"]).details["count"] == 1
    final = client.get(f"/compliance/results/{ids['agent']}").text
    assert "Eccezione scaduta" in final and "TEST router in dismissione" in final
    print("Compliance findings smoke passed")


if __name__ == "__main__":
    main()
