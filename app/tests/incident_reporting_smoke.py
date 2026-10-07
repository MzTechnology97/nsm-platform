"""INC-04: incidents in periodic reports and archived, hashed incident evidence."""
import hashlib
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.incident_models import Incident, IncidentDevice, IncidentHypothesis, IncidentNote
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.report_builder import collect_report_data, render_pdf
from app.report_models import GeneratedReport
from app.security import hash_password

PASSWORD = "CI-Incident-Report-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(username):
    client = TestClient(app)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow().replace(microsecond=0)
    with SessionLocal() as db:
        tech = User(username=f"ci-ir-{suffix}", display_name="CI Report Tech", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        auditor = User(username=f"ci-ir-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)
        customer = Customer(name=f"CI IncReport {suffix}", code=f"IR{suffix[:6]}")
        other = Customer(name=f"CI IncReport Other {suffix}", code=f"IX{suffix[:6]}")
        db.add_all([tech, auditor, customer, other])
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-IR-RTR", status="online", firmware_version="7.19.4")
        db.add(router)
        db.flush()
        resolved = Incident(
            id=uuid.uuid4(), customer_id=customer.id, title="TEST blackout sede", severity="critical", status="resolved",
            started_at=now - timedelta(days=2, hours=4), resolved_at=now - timedelta(days=2), created_at=now, updated_at=now,
        )
        ongoing = Incident(id=uuid.uuid4(), customer_id=customer.id, title="TEST lentezza link", severity="medium", status="investigating", started_at=now - timedelta(hours=5), created_at=now, updated_at=now)
        foreign = Incident(id=uuid.uuid4(), customer_id=other.id, title="TEST foreign incident", severity="low", status="open", started_at=now - timedelta(hours=1), created_at=now, updated_at=now)
        db.add_all([resolved, ongoing, foreign])
        db.flush()
        db.add(IncidentDevice(incident_id=resolved.id, device_id=router.id))
        cause = IncidentHypothesis(
            id=uuid.uuid4(), incident_id=resolved.id, statement="TEST UPS guasto nel rack", category="power", origin="operator",
            status="confirmed", decided_at=now - timedelta(days=1), decision_note="TEST log UPS allegato",
        )
        db.add(cause)
        db.flush()
        resolved.root_cause_hypothesis_id = cause.id
        db.add(IncidentNote(incident_id=resolved.id, kind="action", body="TEST sostituita batteria UPS", occurred_at=now - timedelta(days=2, hours=1)))
        db.add(AuditEvent(event_type="TEST_RTR_REBOOT", device_id=router.id, customer_id=customer.id, timestamp=now - timedelta(days=2, hours=3), result="success", source="ci"))
        db.commit()
        ids = {"resolved": resolved.id, "customer": customer.id}

        data = collect_report_data(db, customer=customer, period_start=(now - timedelta(days=7)).date(), period_end=now.date())
        incidents = data["incidents"]
        assert incidents["total"] == 2 and incidents["resolved_in_period"] == 1, incidents
        assert incidents["mean_hours_to_resolve"] == 4.0
        assert incidents["root_cause_confirmed"] == 1 and incidents["root_cause_pending"] == 1
        assert incidents["by_root_cause"] == {"Alimentazione": 1}
        assert all(row["title"] != "TEST foreign incident" for row in incidents["rows"])
        pdf = render_pdf(data, report_id="TEST", generated_at=now, generated_by="ci", platform_name="NSM").decode("latin-1")
        for marker in ("7. Incidenti", "10. Apparati", "TEST blackout sede", "da confermare", "4.0 ore"):
            assert marker in pdf, marker
        # Incidents outside the period are not reported.
        old = collect_report_data(db, customer=customer, period_start=(now - timedelta(days=60)).date(), period_end=(now - timedelta(days=30)).date())
        assert old["incidents"]["total"] == 0

    tech_client = login(f"ci-ir-{suffix}")
    url = f"/incidents/{ids['resolved']}"
    page = tech_client.get(url).text
    assert "Esporta evidenza PDF" in page and "Nessuna evidenza esportata" in page
    exported = tech_client.post(f"{url}/export", data={"csrf": csrf_from(page)})
    assert "Evidenza esportata" in exported.text

    with SessionLocal() as db:
        report = db.query(GeneratedReport).filter_by(report_type="incident_evidence", customer_id=ids["customer"]).one()
        assert hashlib.sha256(report.content).hexdigest() == report.sha256
        assert report.summary["incident_id"] == str(ids["resolved"]) and report.summary["root_cause_confirmed"] is True
        text = report.content.decode("latin-1")
        for marker in ("TEST blackout sede", "Causa confermata", "TEST UPS guasto nel rack", "TEST log UPS allegato", "TEST sostituita batteria UPS", "Test rtr reboot", "Nota", "Fatto", "TEST-IR-RTR"):
            assert marker in text, marker
        assert db.query(AuditEvent).filter_by(event_type="INCIDENT_EVIDENCE_EXPORTED", customer_id=ids["customer"]).count() == 1
        report_id = report.id

    download = tech_client.get(f"/audit/reports/{report_id}/download")
    assert download.status_code == 200 and download.content.startswith(b"%PDF")
    assert hashlib.sha256(download.content).hexdigest() == download.headers["x-content-sha256"]
    detail = tech_client.get(url).text
    assert f"/audit/reports/{report_id}/download" in detail and "confermata" in detail
    archive = tech_client.get("/audit/reports").text
    assert "Evidenza incidente: TEST blackout sede" in archive and f"/incidents/{ids['resolved']}" in archive

    auditor_client = login(f"ci-ir-aud-{suffix}")
    view = auditor_client.get(url).text
    assert "Esporta evidenza PDF" not in view and f"/audit/reports/{report_id}/download" in view
    assert auditor_client.post(f"{url}/export", data={"csrf": csrf_from(view)}).status_code == 403
    print("Incident reporting smoke passed")


if __name__ == "__main__":
    main()
