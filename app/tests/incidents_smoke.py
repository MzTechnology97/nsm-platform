"""INC-01/02: incident lifecycle and a deterministic, evidence-only timeline."""
import re
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.agent_models import DeviceJob
from app.config import settings
from app.db import SessionLocal
from app.entrypoint import app
from app.incident_models import Incident, IncidentDevice
from app.incident_timeline import build_timeline
from app.models import ActionIssue, AuditEvent, BackupRun, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Incidents-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(username):
    client = TestClient(app)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    return client


def local(value):
    return value.astimezone(ZoneInfo(settings.app_timezone)).strftime("%Y-%m-%dT%H:%M")


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow().replace(second=0, microsecond=0)
    started = now - timedelta(hours=2)
    with SessionLocal() as db:
        tech = User(username=f"ci-inc-tech-{suffix}", display_name="CI Tecnico Inc", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        auditor = User(username=f"ci-inc-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)
        customer = Customer(name=f"CI Incident {suffix}", code=f"IN{suffix[:6]}")
        other = Customer(name=f"CI Incident Other {suffix}", code=f"IO{suffix[:6]}")
        db.add_all([tech, auditor, customer, other])
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-INC-RTR", status="offline")
        cpe = Device(customer_id=customer.id, vendor="ubiquiti", device_type="cpe", name="TEST-INC-CPE", status="online")
        bystander = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-INC-BYSTANDER", status="online")
        foreign = Device(customer_id=other.id, vendor="mikrotik", device_type="router", name="TEST-INC-FOREIGN", status="online")
        db.add_all([router, cpe, bystander, foreign])
        db.flush()
        db.add_all([
            AuditEvent(event_type="CONFIG_DRIFT_DETECTED", device_id=router.id, customer_id=customer.id, timestamp=started - timedelta(hours=1), severity="warning", result="success", source="ci"),
            AuditEvent(event_type="TEST_TOO_OLD", device_id=router.id, customer_id=customer.id, timestamp=started - timedelta(hours=10), result="success", source="ci"),
            AuditEvent(event_type="TEST_CUSTOMER_LEVEL", customer_id=customer.id, timestamp=started + timedelta(minutes=1), result="success", source="ci"),
            AuditEvent(event_type="TEST_BYSTANDER", device_id=bystander.id, customer_id=customer.id, timestamp=started + timedelta(minutes=2), result="success", source="ci"),
            AuditEvent(event_type="TEST_FOREIGN", device_id=foreign.id, customer_id=other.id, timestamp=started + timedelta(minutes=3), result="success", source="ci"),
            BackupRun(device_id=router.id, status="failed", backup_type="mikrotik_multi", started_at=started + timedelta(minutes=4), completed_at=started + timedelta(minutes=5), error_message="TEST timeout"),
            ActionIssue(category="monitoring", severity="high", status="resolved", title="TEST router offline", customer_id=customer.id, device_id=router.id, created_at=started + timedelta(minutes=10), resolved_at=started + timedelta(minutes=40)),
            DeviceJob(device_id=cpe.id, job_type="diagnostic_ping", status="failed", created_at=started + timedelta(minutes=14), completed_at=started + timedelta(minutes=15), last_error="TEST unreachable"),
        ])
        db.commit()
        ids = {"router": router.id, "cpe": cpe.id, "customer": customer.id, "foreign": foreign.id}

    tech_client = login(f"ci-inc-tech-{suffix}")
    headers = {"X-CSRF-Token": tech_client.get("/api/ui/v1/session").json()["csrf_token"]}
    assert tech_client.get(f"/api/ui/v1/devices/{ids['router']}").json()["actions"]["incident"]
    form = tech_client.get(f"/api/ui/v1/incident-form?device={ids['router']}").json()
    names = {device["name"] for device in form["devices"]}
    assert "TEST-INC-RTR" in names and "TEST-INC-FOREIGN" not in names and form["preselected"] == [str(ids["router"])]

    def code(response):
        assert response.status_code == 422, response.text
        return response.json()["detail"]["code"]

    base = {"customer_id": str(ids["customer"]), "title": "TEST perdita connettività", "severity": "high", "summary": "Il cliente segnala assenza di servizio"}
    assert code(tech_client.post("/api/ui/v1/incidents", headers=headers, json={**base, "started_at": local(started), "device_ids": [str(ids["router"]), str(ids["foreign"])]})) == "devices"
    assert code(tech_client.post("/api/ui/v1/incidents", headers=headers, json={**base, "started_at": local(now + timedelta(hours=3)), "device_ids": [str(ids["router"])]})) == "started"
    created = tech_client.post("/api/ui/v1/incidents", headers=headers, json={**base, "started_at": local(started), "device_ids": [str(ids["router"]), str(ids["cpe"])]})
    assert created.status_code == 200, created.text
    incident_id = uuid.UUID(created.json()["id"])
    incident_api = f"/api/ui/v1/incidents/{incident_id}"

    with SessionLocal() as db:
        incident = db.get(Incident, incident_id)
        assert incident.status == "open" and incident.severity == "high" and incident.started_at == started
        timeline = build_timeline(db, incident, [ids["router"], ids["cpe"]], now)
        titles = [entry.title for entry in timeline.entries]
        assert "Config drift detected" in titles and "Test customer level" in titles
        assert "Test too old" not in titles and "Test bystander" not in titles and "Test foreign" not in titles, titles
        assert "Backup fallito" in titles and "Job diagnostic ping fallito" in titles
        assert "Segnalazione aperta: TEST router offline" in titles and "Segnalazione risolta: TEST router offline" in titles
        times = [entry.at for entry in timeline.entries]
        assert times == sorted(times), "chronological"
        assert all(entry.kind == "fact" for entry in timeline.entries)
        again = build_timeline(db, incident, [ids["router"], ids["cpe"]], now)
        assert [e.key for e in again.entries] == [e.key for e in timeline.entries], "deterministic"

    # Operator notes are added to the timeline and labelled as such.
    note_time = started + timedelta(minutes=20)
    noted = tech_client.post(f"{incident_api}/notes", headers=headers, json={"kind": "action", "body": "TEST riavviato il CPE da remoto", "occurred_at": local(note_time)})
    assert noted.status_code == 200, noted.text
    entries = tech_client.get(incident_api).json()["timeline"]["entries"]
    titles = [entry["title"] for entry in entries]
    note = next(entry for entry in entries if entry["kind"] == "operator")
    assert note["title"] == "TEST riavviato il CPE da remoto" or "TEST riavviato il CPE da remoto" in (note["detail"] or note["title"])
    assert note["actor"] == "CI Tecnico Inc"
    position = entries.index(note)
    assert titles.index("Job diagnostic ping fallito") < position < titles.index("Segnalazione risolta: TEST router offline")

    # Lifecycle: investigating → resolved, then list counts.
    assert tech_client.post(f"{incident_api}/status", headers=headers, json={"status": "investigating"}).json()["status"] == "investigating"
    assert code(tech_client.post(f"{incident_api}/status", headers=headers, json={"status": "resolved", "resolved_at": local(started - timedelta(hours=1))})) == "invalid_resolved_at"
    resolved_at = started + timedelta(minutes=50)
    assert tech_client.post(f"{incident_api}/status", headers=headers, json={"status": "resolved", "resolved_at": local(resolved_at)}).json()["status"] == "resolved"
    with SessionLocal() as db:
        incident = db.get(Incident, incident_id)
        assert incident.status == "resolved" and incident.resolved_at == resolved_at
        events = {e.event_type for e in db.query(AuditEvent).filter(AuditEvent.customer_id == ids["customer"], AuditEvent.event_type.like("INCIDENT_%"))}
        assert {"INCIDENT_CREATED", "INCIDENT_NOTE_ADDED", "INCIDENT_STATUS_CHANGED"} <= events
    listing = tech_client.get(f"/api/ui/v1/incidents?customer={ids['customer']}&status=resolved").json()
    assert [item["title"] for item in listing["items"]] == ["TEST perdita connettività"] and listing["items"][0]["resolved_at"]
    assert tech_client.get(f"/api/ui/v1/incidents?customer={ids['customer']}").json()["items"] == []

    # Devices can be changed only within the customer.
    assert code(tech_client.post(f"{incident_api}/devices", headers=headers, json={"device_ids": [str(ids["foreign"])]})) == "foreign_devices"
    assert tech_client.post(f"{incident_api}/devices", headers=headers, json={"device_ids": [str(ids["router"])]}).status_code == 200
    with SessionLocal() as db:
        assert [link.device_id for link in db.query(IncidentDevice).filter_by(incident_id=incident_id)] == [ids["router"]]

    # Read-only roles see the incident but cannot change it.
    auditor_client = login(f"ci-inc-aud-{suffix}")
    view = auditor_client.get(incident_api).json()
    assert view["can_write"] is False and any(entry["kind"] == "operator" for entry in view["timeline"]["entries"])
    auditor_headers = {"X-CSRF-Token": auditor_client.get("/api/ui/v1/session").json()["csrf_token"]}
    assert auditor_client.post(f"{incident_api}/notes", headers=auditor_headers, json={"body": "x"}).status_code == 403
    assert auditor_client.get("/api/ui/v1/incident-form").status_code == 403
    print("Incidents smoke passed")


if __name__ == "__main__":
    main()
