"""INC-03: heuristic correlations stay hypotheses until an operator confirms a root cause."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.incident_correlation import suggest
from app.incident_models import Incident, IncidentDevice, IncidentHypothesis
from app.incident_timeline import build_timeline
from app.models import ActionIssue, AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Root-Cause-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow().replace(microsecond=0)
    started = now - timedelta(hours=3)
    with SessionLocal() as db:
        tech = User(username=f"ci-rc-{suffix}", display_name="CI RC", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        customer = Customer(name=f"CI RootCause {suffix}", code=f"RC{suffix[:6]}")
        db.add_all([tech, customer])
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-RC-RTR", status="offline")
        db.add(router)
        db.flush()
        db.add_all([
            # A configuration change 20 minutes before the start: medium candidate.
            AuditEvent(event_type="CONFIG_BASELINE_APPROVED", device_id=router.id, customer_id=customer.id, timestamp=started - timedelta(minutes=20), result="success", source="ci"),
            # A firmware job 4 hours before: low candidate.
            DeviceJob(device_id=router.id, job_type="firmware_upgrade", status="success", created_at=started - timedelta(hours=4, minutes=5), completed_at=started - timedelta(hours=4)),
            # Issue opened 5 minutes after the start: medium candidate.
            ActionIssue(category="monitoring", severity="high", status="open", title="TEST agent offline", customer_id=customer.id, device_id=router.id, created_at=started + timedelta(minutes=5)),
            # Failed backup 10 minutes after: low candidate (probably a symptom).
            BackupRun(device_id=router.id, status="failed", backup_type="mikrotik_multi", started_at=started + timedelta(minutes=9), completed_at=started + timedelta(minutes=10), error_message="TEST timeout"),
            # Unrelated event far after the start: not a candidate.
            AuditEvent(event_type="USER_LOGIN", customer_id=customer.id, timestamp=started + timedelta(hours=2), result="success", source="ci"),
        ])
        incident = Incident(id=uuid.uuid4(), customer_id=customer.id, title="TEST router down", severity="high", status="open", started_at=started, created_by_user_id=tech.id, created_at=now, updated_at=now)
        db.add(incident)
        db.flush()
        db.add(IncidentDevice(incident_id=incident.id, device_id=router.id))
        db.commit()
        incident_id = incident.id

        timeline = build_timeline(db, incident, [router.id], now)
        candidates = suggest(incident, timeline)
        statements = [c.statement for c in candidates]
        assert len(candidates) == 4, statements
        assert [c.confidence for c in candidates][:2] == ["medium", "medium"], [(c.statement, c.confidence) for c in candidates]
        assert {c.category for c in candidates} >= {"configuration", "connectivity", "firmware"}
        assert not any("User login" in s for s in statements)
        config_key = next(c.key for c in candidates if c.category == "configuration")
        issue_key = next(c.key for c in candidates if c.category == "connectivity")

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-rc-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    url = f"/api/ui/v1/incidents/{incident_id}"
    page = client.get(url).json()
    assert len(page["candidates"]) >= 3 and page["hypotheses"] == []
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}

    def post(path, **body):
        return client.post(path, headers=headers, json=body)

    def code(response):
        assert response.status_code == 422, response.text
        return response.json()["detail"]["code"]

    # Nothing becomes a root cause by itself.
    with SessionLocal() as db:
        assert db.get(Incident, incident_id).root_cause_hypothesis_id is None

    # Record two suggestions and one operator hypothesis.
    assert post(f"{url}/hypotheses", candidate_key=config_key).status_code == 200
    assert post(f"{url}/hypotheses", candidate_key=issue_key).status_code == 200
    assert code(post(f"{url}/hypotheses", candidate_key="forged-key")) == "candidate_gone"
    assert post(f"{url}/hypotheses", statement="Alimentazione instabile nel rack", category="power").status_code == 200
    with SessionLocal() as db:
        rows = {h.statement: h for h in db.query(IncidentHypothesis).filter_by(incident_id=incident_id)}
        assert len(rows) == 3 and all(h.status == "proposed" for h in rows.values())
        suggested = [h for h in rows.values() if h.origin == "suggested"]
        assert len(suggested) == 2 and all(h.evidence and h.evidence[0]["key"] for h in suggested)
        config_h = next(h for h in suggested if h.category == "configuration")
        power_h = rows["Alimentazione instabile nel rack"]
        ids = {"config": config_h.id, "power": power_h.id}
    remaining = {c["key"] for c in client.get(url).json()["candidates"]}
    assert config_key not in remaining and issue_key not in remaining, "used candidates are not suggested again"

    # Confirming requires evidence; then only the confirmed hypothesis is the root cause.
    decision = f"{url}/hypotheses/{ids['config']}/decision"
    assert code(post(decision, decision="confirm")) == "decision_note_required"
    assert post(decision, decision="confirm", note="Il cambio di baseline ha disattivato la route di default").status_code == 200
    with SessionLocal() as db:
        incident = db.get(Incident, incident_id)
        assert incident.root_cause_hypothesis_id == ids["config"]
        assert db.get(IncidentHypothesis, ids["config"]).decided_by_user_id is not None

    # Confirming another one replaces it; the previous goes back to proposed.
    other = f"{url}/hypotheses/{ids['power']}/decision"
    assert post(other, decision="confirm", note="Log UPS conferma il blackout").status_code == 200
    with SessionLocal() as db:
        assert db.get(Incident, incident_id).root_cause_hypothesis_id == ids["power"]
        assert db.get(IncidentHypothesis, ids["config"]).status == "proposed"
    # Rejecting the confirmed one clears the root cause.
    assert post(other, decision="reject", note="UPS in realtà non coinvolto").status_code == 200
    with SessionLocal() as db:
        assert db.get(Incident, incident_id).root_cause_hypothesis_id is None
        events = {e.event_type for e in db.query(AuditEvent).filter(AuditEvent.customer_id == db.get(Incident, incident_id).customer_id, AuditEvent.event_type.like("INCIDENT_%"))}
        assert {"INCIDENT_HYPOTHESIS_ADDED", "INCIDENT_ROOT_CAUSE_CONFIRMED", "INCIDENT_HYPOTHESIS_REJECTED"} <= events

    assert post(decision, decision="confirm", note="Verificato sul router").status_code == 200
    listing = client.get("/api/ui/v1/incidents?status=all&q=TEST+router+down").json()["items"]
    assert listing[0]["root_cause"]["category"] == "configuration"
    detail = client.get(url).json()
    confirmed = next(h for h in detail["hypotheses"] if h["status"] == "confirmed")
    assert confirmed["decision_note"] == "Verificato sul router"
    print("Incident root cause smoke passed")


if __name__ == "__main__":
    main()
