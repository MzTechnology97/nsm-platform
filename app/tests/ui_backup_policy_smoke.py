"""New interface, backup policy forms: form data (new, customer preset, edit, clone), create/update with coded errors, customer limits, toggle, delete, permissions."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backup_models import BackupPolicySettings
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, BackupPolicy, Customer, Device, Site, User
from app.security import hash_password

PASSWORD = "CI-UI-Backup-Policy-2026"


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
        mine = Customer(name=f"CI Policy {suffix}", code=f"PO{suffix[:6]}")
        other = Customer(name=f"CI Policy other {suffix}", code=f"PP{suffix[:6]}")
        db.add_all([mine, other])
        db.flush()
        site = Site(customer_id=mine.id, name=f"CI Site {suffix}")
        foreign_site = Site(customer_id=other.id, name=f"CI Foreign {suffix}")
        db.add_all([site, foreign_site])
        db.flush()
        router = Device(customer_id=mine.id, site_id=site.id, vendor="mikrotik", device_type="router", name=f"TEST-PO-RTR-{suffix}", status="online")
        foreign = Device(customer_id=other.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-PO-CPE-{suffix}", status="online")
        db.add_all([router, foreign])
        db.add_all([
            User(username=f"ci-po-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-po-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        ids = {"mine": str(mine.id), "other": str(other.id), "site": str(site.id), "foreign_site": str(foreign_site.id),
               "router": str(router.id), "foreign": str(foreign.id)}

    base = "/api/ui/v1/backup-policies"
    assert TestClient(app).get(f"{base}/form").status_code == 401
    admin, csrf = login(f"ci-po-{suffix}")
    auditor, auditor_csrf = login(f"ci-po-au-{suffix}")
    assert auditor.get(f"{base}/form").status_code == 403

    blank = admin.get(f"{base}/form").json()
    assert blank["mode"] == "create" and blank["state"]["scope_type"] == "global" and blank["context"] is None
    assert blank["state"]["options"]["mikrotik_export"] is True and blank["state"]["retention_daily"] == 30
    assert {"key": "mikrotik", "label": "MikroTik"} in blank["vendors"]
    router_row = next(d for d in blank["devices"] if d["id"] == ids["router"])
    assert router_row["brand"]["symbol"] == "brand-mikrotik" and router_row["customer_id"] == ids["mine"]

    preset = admin.get(f"{base}/form?customer={ids['mine']}&device={ids['router']}").json()
    assert preset["context"]["id"] == ids["mine"] and preset["state"]["scope_type"] == "device"
    assert preset["state"]["device_id"] == ids["router"] and preset["state"]["vendor"] == "mikrotik"
    assert [c["id"] for c in preset["customers"]] == [ids["mine"]] and all(d["customer_id"] == ids["mine"] for d in preset["devices"])
    assert admin.get(f"{base}/form?customer={ids['mine']}&site={ids['site']}").json()["state"]["scope_type"] == "site"
    assert code(admin.get(f"{base}/form?customer={ids['mine']}&device={ids['foreign']}")) == "customer_scope"
    assert admin.get(f"{base}/form?customer={uuid.uuid4()}").status_code == 404

    body = {"name": f"CI weekly {suffix}", "description": "TEST policy", "is_enabled": True, "scope_type": "vendor", "vendor": "mikrotik",
            "schedule_kind": "weekly", "backup_time": "02:30", "weekday": 2, "monthday": 1,
            "retention_daily": 14, "retention_weekly": 8, "retention_monthly": 6, "retry_count": 2,
            "mikrotik_binary": False, "mikrotik_export": True, "verify_hash": True, "pre_firmware": False}
    assert auditor.post(base, headers=auditor_csrf, json=body).status_code == 403
    assert admin.post(base, json=body).status_code == 403, "CSRF header required"
    assert code(admin.post(base, headers=csrf, json={**body, "name": " "})) == "name"
    assert code(admin.post(base, headers=csrf, json={**body, "backup_time": "25:00"})) == "time"
    assert code(admin.post(base, headers=csrf, json={**body, "schedule_kind": "hourly"})) == "frequency"
    assert code(admin.post(base, headers=csrf, json={**body, "schedule_kind": "monthly", "monthday": 31})) == "monthday"
    assert code(admin.post(base, headers=csrf, json={**body, "vendor": "acme"})) == "vendor"
    assert code(admin.post(base, headers=csrf, json={**body, "scope_type": "site", "site_id": "nope"})) == "site"
    assert code(admin.post(base, headers=csrf, json={**body, "scope_type": "galaxy"})) == "scope"
    assert code(admin.post(base, headers=csrf, json={**body, "retention_daily": "many"})) == "retention"
    assert code(admin.post(base, headers=csrf, json={**body, "scope_type": "device", "customer_id": ids["mine"], "device_id": ids["foreign"]})) == "device_customer"
    created = admin.post(base, headers=csrf, json=body).json()
    assert code(admin.post(base, headers=csrf, json=body)) == "duplicate"
    with SessionLocal() as db:
        policy = db.get(BackupPolicy, uuid.UUID(created["id"]))
        settings = db.get(BackupPolicySettings, policy.id)
        assert policy.schedule_cron == "30 2 * * 2" and policy.vendor == "mikrotik" and policy.binary_backup is False and policy.text_export is True
        assert settings.schedule_kind == "weekly" and settings.description == "TEST policy" and policy.retention_daily == 14 and policy.retry_count == 2

    # Customer context: only that customer, its sites and devices.
    context = {**body, "name": f"CI customer {suffix}", "context_customer_id": ids["mine"]}
    assert code(admin.post(base, headers=csrf, json={**context, "scope_type": "global"})) == "customer_scope"
    assert code(admin.post(base, headers=csrf, json={**context, "scope_type": "device", "customer_id": ids["other"], "device_id": ids["foreign"]})) == "customer_scope"
    assert code(admin.post(base, headers=csrf, json={**context, "scope_type": "site", "customer_id": ids["mine"], "site_id": ids["foreign_site"]})) == "site_customer"
    device_policy = admin.post(base, headers=csrf, json={**context, "scope_type": "device", "customer_id": ids["mine"], "device_id": ids["router"]}).json()
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "BACKUP_POLICY_CREATED", AuditEvent.customer_id == uuid.UUID(ids["mine"])))
        assert event and event.details["policy_id"] == device_policy["id"]

    # Edit and clone forms.
    edit = admin.get(f"{base}/form?policy={created['id']}").json()
    assert edit["mode"] == "edit" and edit["policy"]["name"] == body["name"] and edit["state"]["schedule_weekday"] == 2 and edit["state"]["id"] == created["id"]
    clone = admin.get(f"{base}/form?policy={created['id']}&clone=1").json()
    assert clone["mode"] == "clone" and clone["state"]["id"] is None and clone["state"]["name"] == f"Copia di {body['name']}"
    assert admin.get(f"{base}/form?policy={uuid.uuid4()}").status_code == 404

    url = f"{base}/{created['id']}"
    assert code(admin.put(url, headers=csrf, json={**body, "name": f"CI customer {suffix}"})) == "duplicate"
    updated = admin.put(url, headers=csrf, json={**body, "name": f"CI daily {suffix}", "schedule_kind": "daily", "backup_time": "04:15"}).json()
    assert updated["name"] == f"CI daily {suffix}"
    with SessionLocal() as db:
        assert db.get(BackupPolicy, uuid.UUID(created["id"])).schedule_cron == "15 4 * * *"
    assert admin.put(f"{base}/{uuid.uuid4()}", headers=csrf, json=body).status_code == 404

    assert admin.post(f"{url}/toggle", headers=csrf).json() == {"enabled": False}
    assert admin.get(f"{base}/form?policy={created['id']}").json()["policy"]["enabled"] is False
    assert admin.post(f"{url}/toggle", headers=csrf).json() == {"enabled": True}
    assert auditor.post(f"{url}/toggle", headers=auditor_csrf).status_code == 403

    assert code(admin.post(f"{url}/delete", headers=csrf, json={"confirm_name": "wrong"})) == "confirm"
    assert admin.post(f"{url}/delete", headers=csrf, json={"confirm_name": f"CI daily {suffix}"}).json() == {"deleted": True}
    with SessionLocal() as db:
        assert db.get(BackupPolicy, uuid.UUID(created["id"])) is None
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "BACKUP_POLICY_DELETED", AuditEvent.details["policy_id"].as_string() == created["id"]))

    for path in ("/app/backups/policies/new", f"/app/backups/policies/{device_policy['id']}/edit", f"/app/backups/policies/{device_policy['id']}/clone"):
        assert admin.get(path).status_code in (200, 503)
    print("ui backup policy smoke ok")


if __name__ == "__main__":
    main()
