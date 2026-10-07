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
    device_page = tech_client.get(f"/devices/{ids['router']}").text
    assert f"/incidents/new?device={ids['router']}" in device_page
    form = tech_client.get(f"/incidents/new?device={ids['router']}").text
    assert "TEST-INC-RTR" in form and "TEST-INC-FOREIGN" not in form and "checked" in form
    token = csrf_from(form)

    base = {"csrf": token, "customer_id": str(ids["customer"]), "title": "TEST perdita connettività", "severity": "high", "summary": "Il cliente segnala assenza di servizio"}
    bad = tech_client.post("/incidents", data={**base, "started_at": local(started), "device_ids": [str(ids["router"]), str(ids["foreign"])]})
    assert "devono appartenere al cliente" in bad.text
    future = tech_client.post("/incidents", data={**base, "started_at": local(now + timedelta(hours=3)), "device_ids": [str(ids["router"])]})
    assert "non nel futuro" in future.text
    created = tech_client.post("/incidents", data={**base, "started_at": local(started), "device_ids": [str(ids["router"]), str(ids["cpe"])]}, follow_redirects=False)
    assert created.status_code == 303, created.text
    incident_url = created.headers["location"]
    incident_id = uuid.UUID(incident_url.rsplit("/", 1)[-1])

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
    page = tech_client.get(incident_url).text
    assert "Fatti osservati" in page and "Note operatore" in page
    noted = tech_client.post(f"{incident_url}/notes", data={"csrf": token, "kind": "action", "body": "TEST riavviato il CPE da remoto", "occurred_at": local(note_time)})
    assert "Nota aggiunta" in noted.text
    detail = tech_client.get(incident_url).text
    assert "TEST riavviato il CPE da remoto" in detail and "CI Tecnico Inc" in detail and "timeline-operator" in detail
    assert detail.index("Job diagnostic ping fallito") < detail.index("TEST riavviato il CPE da remoto") < detail.index("Segnalazione risolta: TEST router offline")
    only_notes = tech_client.get(f"{incident_url}?view=notes").text.split('id="timeline"', 1)[1]
    assert "TEST riavviato il CPE da remoto" in only_notes and "Backup fallito" not in only_notes
    assert "Inizio incidente" in detail

    # Lifecycle: investigating → resolved, then list counts.
    assert "Incidente aggiornato" in tech_client.post(f"{incident_url}/status", data={"csrf": token, "status": "investigating"}).text
    assert "successiva all" in tech_client.post(f"{incident_url}/status", data={"csrf": token, "status": "resolved", "resolved_at": local(started - timedelta(hours=1))}).text
    resolved_at = started + timedelta(minutes=50)
    assert "Incidente aggiornato" in tech_client.post(f"{incident_url}/status", data={"csrf": token, "status": "resolved", "resolved_at": local(resolved_at)}).text
    with SessionLocal() as db:
        incident = db.get(Incident, incident_id)
        assert incident.status == "resolved" and incident.resolved_at == resolved_at
        events = {e.event_type for e in db.query(AuditEvent).filter(AuditEvent.customer_id == ids["customer"], AuditEvent.event_type.like("INCIDENT_%"))}
        assert {"INCIDENT_CREATED", "INCIDENT_NOTE_ADDED", "INCIDENT_STATUS_CHANGED"} <= events
    listing = tech_client.get(f"/incidents?customer={ids['customer']}&status=resolved").text
    assert "TEST perdita connettività" in listing and "50 min" in listing
    assert "Nessun incidente attivo" in tech_client.get(f"/incidents?customer={ids['customer']}").text

    # Devices can be changed only within the customer.
    assert "devono appartenere" in tech_client.post(f"{incident_url}/devices", data={"csrf": token, "device_ids": [str(ids["foreign"])]}).text
    assert "Apparati coinvolti aggiornati" in tech_client.post(f"{incident_url}/devices", data={"csrf": token, "device_ids": [str(ids["router"])]}).text
    with SessionLocal() as db:
        assert [link.device_id for link in db.query(IncidentDevice).filter_by(incident_id=incident_id)] == [ids["router"]]

    # Read-only roles see the incident but cannot change it.
    auditor_client = login(f"ci-inc-aud-{suffix}")
    view = auditor_client.get(incident_url)
    assert view.status_code == 200 and "Aggiorna stato" not in view.text and "TEST riavviato il CPE" in view.text
    denied = auditor_client.post(f"{incident_url}/notes", data={"csrf": csrf_from(view.text), "body": "x"})
    assert denied.status_code == 403
    assert auditor_client.get("/incidents/new").status_code == 403
    print("Incidents smoke passed")


if __name__ == "__main__":
    main()
