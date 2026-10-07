"""LIFE-03: EOL/EOS remediation decisions, Action Center issues, history and report."""
import csv
import io
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.lifecycle_remediation import ISSUE_TITLE, housekeeping
from app.models import ActionIssue, AuditEvent, Customer, Device, LifecycleRemediation, LifecycleRemediationHistory, User, utcnow
from app.report_builder import collect_report_data, render_csv, render_pdf
from app.security import hash_password

PASSWORD = "CI-Lifecycle-Remediation-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def open_issue(db, device_id):
    return db.scalar(select(ActionIssue).where(ActionIssue.device_id == device_id, ActionIssue.title == ISSUE_TITLE, ActionIssue.status.in_(["open", "acknowledged"])))


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    today = now.date()
    with SessionLocal() as db:
        tech = User(username=f"ci-lr-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        auditor = User(username=f"ci-lr-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)
        customer = Customer(name=f"CI LifeRem {suffix}", code=f"LR{suffix[:6]}")
        other = Customer(name=f"CI LifeRem Other {suffix}", code=f"LO{suffix[:6]}")
        db.add_all([tech, auditor, customer, other])
        db.flush()
        old = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-LR-OLD", status="online", lifecycle_status="eos",
                     lifecycle_match="manual", lifecycle_source="TEST bollettino", eos_date=today - timedelta(days=30))
        new = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-LR-NEW", status="online", lifecycle_status="supported")
        foreign = Device(customer_id=other.id, vendor="mikrotik", device_type="router", name="TEST-LR-FOREIGN", status="online")
        db.add_all([old, new, foreign])
        db.flush()
        stats = housekeeping(db, now)
        db.commit()
        assert stats["created"] >= 1 and stats["issues_opened"] >= 1
        issue = open_issue(db, old.id)
        assert issue is not None and issue.severity == "critical" and issue.category == "lifecycle"
        ids = {"old": old.id, "new": new.id, "foreign": foreign.id, "customer": customer.id}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-lr-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{ids['old']}/lifecycle").text
    assert "Gestione fuori supporto" in page and "Da gestire" in page and "Pianifica sostituzione" in page
    token = csrf_from(page)
    url = f"/devices/{ids['old']}/lifecycle/remediation"

    assert "non può essere nel passato" in client.post(url, data={"csrf": token, "action": "plan", "target_date": (today - timedelta(days=1)).isoformat(), "note": "TEST piano"}, follow_redirects=True).text
    response = client.post(url, data={"csrf": token, "action": "plan", "target_date": (today + timedelta(days=10)).isoformat(), "note": "TEST sostituzione con hAP ax3"}, follow_redirects=True)
    assert "Sostituzione pianificata entro" in response.text
    with SessionLocal() as db:
        assert db.scalar(select(LifecycleRemediation.status).where(LifecycleRemediation.device_id == ids["old"])) == "planned"
        assert open_issue(db, ids["old"]) is None, "a plan resolves the issue"
        # Twenty days later the plan is overdue: the issue comes back.
        housekeeping(db, now + timedelta(days=20))
        db.commit()
        reopened = open_issue(db, ids["old"])
        assert reopened is not None and "non ancora registrata" in reopened.details["message"]

    assert "tra domani e un anno" in client.post(url, data={"csrf": token, "action": "exception", "exception_until": (today + timedelta(days=500)).isoformat(), "note": "TEST budget prossimo anno"}, follow_redirects=True).text
    response = client.post(url, data={"csrf": token, "action": "exception", "exception_until": (today + timedelta(days=60)).isoformat(), "note": "TEST apparato isolato in DMZ"}, follow_redirects=True)
    assert "Eccezione valida fino al" in response.text
    with SessionLocal() as db:
        assert open_issue(db, ids["old"]) is None
        housekeeping(db, now + timedelta(days=61))
        db.commit()
        rem = db.scalar(select(LifecycleRemediation).where(LifecycleRemediation.device_id == ids["old"]))
        assert rem.status == "open" and open_issue(db, ids["old"]) is not None, "an expired exception is to handle again"

    foreign = client.post(url, data={"csrf": token, "action": "replaced", "replacement_device_id": str(ids["foreign"])}, follow_redirects=True)
    assert "stesso cliente" in foreign.text
    response = client.post(url, data={"csrf": token, "action": "replaced", "replacement_device_id": str(ids["new"]), "note": "TEST installato"}, follow_redirects=True)
    assert "Sostituito da TEST-LR-NEW" in response.text
    with SessionLocal() as db:
        rem = db.scalar(select(LifecycleRemediation).where(LifecycleRemediation.device_id == ids["old"]))
        assert rem.status == "replaced" and rem.replacement_device_id == ids["new"] and open_issue(db, ids["old"]) is None
        actions = [h.action for h in db.scalars(select(LifecycleRemediationHistory).where(LifecycleRemediationHistory.remediation_id == rem.id).order_by(LifecycleRemediationHistory.created_at))]
        assert actions[0] == "created" and {"plan", "exception", "exception_expired", "replaced"} <= set(actions)
        events = set(db.scalars(select(AuditEvent.event_type).where(AuditEvent.device_id == ids["old"])))
        assert {"LIFECYCLE_REPLACEMENT_PLANNED", "LIFECYCLE_EXCEPTION_GRANTED", "LIFECYCLE_DEVICE_REPLACED"} <= events

        customer = db.get(Customer, ids["customer"])
        data = collect_report_data(db, customer=customer, period_start=today - timedelta(days=7), period_end=today)
        assert data["lifecycle"]["remediation"]["counts"].get("Sostituito") == 1
        rows = {r["device"]: r for r in csv.DictReader(io.StringIO(render_csv(data).decode("utf-8")))}
        assert rows["TEST-LR-OLD"]["lifecycle_remediation"] == "Sostituito"
        pdf = render_pdf(data, report_id="TEST", generated_at=now, generated_by="ci", platform_name="NSM").decode("latin-1")
        assert "Gestione apparati fuori supporto" in pdf

    assert "Riapri la gestione" in client.get(f"/devices/{ids['old']}/lifecycle").text
    assert ">Sostituito<" in client.get(f"/security/lifecycle?customer={ids['customer']}&state=eos").text

    reader = TestClient(app)
    assert reader.post("/login", data={"username": f"ci-lr-aud-{suffix}", "password": PASSWORD, "csrf": csrf_from(reader.get("/login").text)}, follow_redirects=False).status_code == 303
    view = reader.get(f"/devices/{ids['old']}/lifecycle")
    assert view.status_code == 200 and "Gestione fuori supporto" in view.text and "Pianifica sostituzione" not in view.text and "Salva valore manuale" not in view.text
    assert reader.post(url, data={"csrf": csrf_from(view.text), "action": "reopen"}).status_code == 403
    print("Lifecycle remediation smoke passed")


if __name__ == "__main__":
    main()
