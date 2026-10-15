"""New interface, new incident: form data (customer choice, device preset, prefill), create with coded checks, scope, permissions."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.incident_models import Incident, IncidentDevice
from app.models import AuditEvent, Customer, Device, Site, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Incident-New-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def code(response):
    assert response.status_code == 422, response.text
    return response.json()["detail"]["code"]


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        mine = Customer(name=f"CI UI Incident {suffix}", code=f"IN{suffix}")
        other = Customer(name=f"CI UI Incident other {suffix}", code=f"IO{suffix}")
        db.add_all([mine, other])
        db.flush()
        site = Site(customer_id=mine.id, name=f"CI Site {suffix}")
        db.add(site)
        db.flush()
        router = Device(customer_id=mine.id, site_id=site.id, vendor="mikrotik", device_type="router", name=f"TEST-IN-RTR-{suffix}", status="online")
        cpe = Device(customer_id=mine.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-IN-CPE-{suffix}", status="offline")
        foreign = Device(customer_id=other.id, vendor="tp-link", device_type="router", name=f"TEST-IN-FOR-{suffix}", status="online")
        db.add_all([router, cpe, foreign])
        db.add_all([
            User(username=f"ci-in-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
            User(username=f"ci-in-a-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-in-s-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {"mine": str(mine.id), "other": str(other.id), "site": str(site.id), "router": str(router.id), "cpe": str(cpe.id), "foreign": str(foreign.id)}

    form_url = "/api/ui/v1/incident-form"
    assert TestClient(app).get(form_url).status_code == 401
    tech, csrf = login(f"ci-in-{suffix}")
    auditor, auditor_csrf = login(f"ci-in-a-{suffix}")
    assert auditor.get(form_url).status_code == 403

    blank = tech.get(form_url).json()
    assert blank["customer"] is None and blank["devices"] == [] and any(c["id"] == ids["mine"] for c in blank["customers"])
    assert blank["severities"] == ["critical", "high", "medium", "low"] and blank["prefill"]["severity"] == "medium"
    preset = tech.get(f"{form_url}?device={ids['router']}&title=TEST+loss&severity=high").json()
    assert preset["customer"]["id"] == ids["mine"] and preset["preselected"] == [ids["router"]] and preset["site_id"] == ids["site"]
    assert preset["prefill"] == {"title": "TEST loss", "summary": "", "severity": "high"}
    assert {d["id"] for d in preset["devices"]} == {ids["router"], ids["cpe"]}
    assert next(d for d in preset["devices"] if d["id"] == ids["cpe"])["brand"]["symbol"] == "brand-ubiquiti"

    create = "/api/ui/v1/incidents"
    now = utcnow()
    body = {"customer_id": ids["mine"], "title": "TEST main site down", "summary": "TEST customer call", "severity": "high",
            "started_at": (now - timedelta(minutes=20)).isoformat(), "site_id": ids["site"], "device_ids": [ids["router"], ids["cpe"]]}
    assert auditor.post(create, headers=auditor_csrf, json=body).status_code == 403
    assert tech.post(create, json=body).status_code == 403, "CSRF header required"
    assert code(tech.post(create, headers=csrf, json={**body, "customer_id": ""})) == "customer"
    assert code(tech.post(create, headers=csrf, json={**body, "title": " "})) == "title"
    assert code(tech.post(create, headers=csrf, json={**body, "started_at": (now + timedelta(days=1)).isoformat()})) == "started"
    assert code(tech.post(create, headers=csrf, json={**body, "started_at": "yesterday"})) == "started"
    assert code(tech.post(create, headers=csrf, json={**body, "device_ids": [ids["router"], ids["foreign"]]})) == "devices"
    created = tech.post(create, headers=csrf, json={**body, "severity": "apocalyptic"}).json()
    with SessionLocal() as db:
        incident = db.get(Incident, uuid.UUID(created["id"]))
        assert incident.title == "TEST main site down" and incident.severity == "medium" and incident.status == "open" and str(incident.site_id) == ids["site"]
        assert {str(link.device_id) for link in db.query(IncidentDevice).filter_by(incident_id=incident.id)} == {ids["router"], ids["cpe"]}
        assert db.query(AuditEvent).filter(AuditEvent.event_type == "INCIDENT_CREATED", AuditEvent.customer_id == incident.customer_id).count() == 1
    assert tech.get(f"/api/ui/v1/incidents/{created['id']}").status_code == 200

    # A user scoped to another customer cannot open incidents on this one.
    scoped, scoped_csrf = login(f"ci-in-s-{suffix}")
    assert code(scoped.post(create, headers=scoped_csrf, json=body)) == "customer"
    assert all(c["id"] != ids["mine"] for c in scoped.get(form_url).json()["customers"])

    for path in ("/app/incidents/new", f"/app/incidents/new?device={ids['router']}"):
        assert tech.get(path).status_code in (200, 503)
    print("ui incident new smoke ok")


if __name__ == "__main__":
    main()
