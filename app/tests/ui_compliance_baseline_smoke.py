"""New interface, compliance baselines: list, default baseline, form data, create/update with coded errors and re-evaluation, permissions."""
import re
import uuid

from fastapi.testclient import TestClient

from app.compliance_models import ComplianceBaseline, ComplianceResult
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-UI-Compliance-Baseline-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def code(response):
    assert response.status_code == 422, response.text
    return response.json()["detail"]["code"]


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        db.query(ComplianceBaseline).delete()
        customer = Customer(name=f"CI UI Baseline {suffix}", code=f"UB{suffix[:6]}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-UB-RTR-{suffix}", status="online",
                        firmware_status="current", lifecycle_status="supported")
        db.add(device)
        db.add_all([
            User(username=f"ci-ub-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
            User(username=f"ci-ub-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        ids = {"customer": str(customer.id), "device": str(device.id)}

    base = "/api/ui/v1/compliance/baselines"
    assert TestClient(app).get(base).status_code == 401
    tech, csrf = login(f"ci-ub-{suffix}")
    auditor, auditor_csrf = login(f"ci-ub-aud-{suffix}")

    listing = auditor.get(base).json()
    assert listing == {"items": [], "has_global": False, "can_manage": False}
    assert auditor.get(f"{base}/form").status_code == 403
    assert auditor.post(f"{base}/default", headers=auditor_csrf).status_code == 403

    created = tech.post(f"{base}/default", headers=csrf).json()
    assert created["devices"] >= 1
    assert code(tech.post(f"{base}/default", headers=csrf)) == "exists"
    listing = tech.get(base).json()
    assert listing["has_global"] and listing["can_manage"] and listing["items"][0]["target"]["type"] == "global"
    assert {c["id"] for c in listing["items"][0]["controls"]} >= {"backup_recent", "firmware_current"}

    form = tech.get(f"{base}/form").json()
    assert form["baseline"] is None and "device" in form["scopes"] and "mikrotik" in form["vendors"]
    control = next(c for c in form["controls"] if c["id"] == "backup_recent")
    assert control["defaults"] == {"max_age_days": 7} and control["params_help"]["max_age_days"]
    assert any(d["id"] == ids["device"] and d["brand"]["symbol"] == "brand-mikrotik" for d in form["devices"])

    body = {"name": f"TEST device baseline {suffix}", "scope_type": "device", "device_id": ids["device"], "enabled": True,
            "controls": {"backup_recent": {"mode": "enabled", "params": {"max_age_days": 30}},
                         "firmware_current": {"mode": "disabled", "params": {"strict": True}},
                         "agent_heartbeat": {"mode": "inherit", "params": {}}}}
    assert tech.post(base, json=body).status_code == 403, "CSRF header required"
    assert code(tech.post(base, headers=csrf, json={**body, "name": " "})) == "name_scope"
    assert code(tech.post(base, headers=csrf, json={**body, "scope_type": "galaxy"})) == "name_scope"
    assert code(tech.post(base, headers=csrf, json={**body, "scope_type": "vendor", "vendor": "acme"})) == "vendor"
    assert code(tech.post(base, headers=csrf, json={**body, "device_id": str(uuid.uuid4())})) == "device"
    assert code(tech.post(base, headers=csrf, json={**body, "scope_type": "customer", "customer_id": ""})) == "customer"
    assert code(tech.post(base, headers=csrf, json={**body, "controls": {"backup_recent": {"mode": "enabled", "params": {"max_age_days": 0}}}})) == "param"
    assert code(tech.post(base, headers=csrf, json={**body, "controls": {"backup_recent": {"mode": "inherit"}}})) == "no_controls"
    saved = tech.post(base, headers=csrf, json=body).json()
    assert saved["version"] == 1
    with SessionLocal() as db:
        baseline = db.get(ComplianceBaseline, uuid.UUID(saved["id"]))
        assert baseline.controls == {"backup_recent": {"enabled": True, "params": {"max_age_days": 30}},
                                     "firmware_current": {"enabled": False, "params": {"strict": True}}}
        assert baseline.device_id == uuid.UUID(ids["device"]) and baseline.is_enabled
        assert db.query(ComplianceResult).filter_by(device_id=uuid.UUID(ids["device"]), control_id="firmware_current").count() == 0, "disabled for this device"
        assert db.query(AuditEvent).filter(AuditEvent.event_type == "COMPLIANCE_BASELINE_SAVED").count() >= 2

    edit = tech.get(f"{base}/form?baseline={saved['id']}").json()
    assert edit["baseline"]["version"] == 1 and edit["settings"]["backup_recent"]["params"]["max_age_days"] == 30
    updated = tech.post(base, headers=csrf, json={**body, "baseline_id": saved["id"], "enabled": False, "description": "TEST paused"}).json()
    assert updated == {"id": saved["id"], "version": 2}
    row = next(r for r in tech.get(base).json()["items"] if r["id"] == saved["id"])
    assert row["enabled"] is False and row["description"] == "TEST paused" and row["target"]["device"]["brand"]["symbol"] == "brand-mikrotik"
    assert tech.get(f"{base}/form?baseline={uuid.uuid4()}").status_code == 404

    for path in ("/app/compliance/baselines", "/app/compliance/baselines/new", f"/app/compliance/baselines/{saved['id']}/edit"):
        assert tech.get(path).status_code in (200, 503)
    print("ui compliance baseline smoke ok")


if __name__ == "__main__":
    main()
