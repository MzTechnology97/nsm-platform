"""New interface: incident evidence export from the detail page, archived exports, Action Center device filter."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.incident_models import Incident, IncidentDevice
from app.models import ActionIssue, AuditEvent, Customer, Device, User, utcnow
from app.report_models import GeneratedReport
from app.security import hash_password

PASSWORD = "CI-UI-Incident-Evidence-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Evidence {suffix}", code=f"EV{suffix}")
        db.add(customer)
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-EV-RTR-{suffix}", status="online")
        other = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-EV-CPE-{suffix}", status="offline")
        db.add_all([router, other])
        db.flush()
        incident = Incident(id=uuid.uuid4(), customer_id=customer.id, title=f"TEST evidence {suffix}", severity="high", status="open",
                            started_at=now - timedelta(hours=1), created_at=now, updated_at=now)
        db.add(incident)
        db.flush()
        db.add(IncidentDevice(incident_id=incident.id, device_id=router.id, added_at=now))
        db.add_all([
            ActionIssue(category="monitoring", severity="high", status="open", title=f"TEST router issue {suffix}", customer_id=customer.id, device_id=router.id, details={}),
            ActionIssue(category="monitoring", severity="warning", status="open", title=f"TEST cpe issue {suffix}", customer_id=customer.id, device_id=other.id, details={}),
        ])
        db.add_all([
            User(username=f"ci-ev-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-ev-t-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
        ])
        db.commit()
        ids = {"incident": str(incident.id), "router": str(router.id), "customer": str(customer.id)}

    admin, csrf = login(f"ci-ev-{suffix}")
    base = f"/api/ui/v1/incidents/{ids['incident']}"
    detail = admin.get(base).json()
    assert detail["can_export"] is True and detail["exports"] == []

    assert admin.post(f"{base}/export").status_code == 403, "CSRF header required"
    exported = admin.post(f"{base}/export", headers=csrf).json()
    assert len(exported["sha256"]) == 64
    detail = admin.get(base).json()
    assert [e["id"] for e in detail["exports"]] == [exported["id"]]
    row = detail["exports"][0]
    assert row["status"] == "open" and row["root_cause_confirmed"] is False and row["download_url"] == f"/audit/reports/{exported['id']}/download"
    download = admin.get(row["download_url"])
    assert download.status_code == 200 and download.content.startswith(b"%PDF")
    with SessionLocal() as db:
        report = db.get(GeneratedReport, uuid.UUID(exported["id"]))
        assert report.sha256 == exported["sha256"] and report.summary["incident_id"] == ids["incident"]
        assert db.query(AuditEvent).filter(AuditEvent.event_type == "INCIDENT_EVIDENCE_EXPORTED").count() >= 1

    tech, tech_csrf = login(f"ci-ev-t-{suffix}")
    if not tech.get(base).json()["can_export"]:
        assert tech.post(f"{base}/export", headers=tech_csrf).status_code == 403
    assert admin.post(f"/api/ui/v1/incidents/{uuid.uuid4()}/export", headers=csrf).status_code == 404

    # Action Center: device filter.
    issues = admin.get(f"/api/ui/v1/action-center?customer={ids['customer']}").json()["items"]
    assert {i["title"] for i in issues} == {f"TEST router issue {suffix}", f"TEST cpe issue {suffix}"}
    only = admin.get(f"/api/ui/v1/action-center?device={ids['router']}").json()
    assert [i["title"] for i in only["items"]] == [f"TEST router issue {suffix}"] and only["total"] == 1

    assert admin.get(f"/app/action-center?device={ids['router']}").status_code in (200, 503)
    print("ui incident evidence smoke ok")


if __name__ == "__main__":
    main()
