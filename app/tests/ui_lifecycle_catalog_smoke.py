"""New interface, lifecycle catalog: list with filters, form, create/update with coded checks and re-correlation, CSV import, delete, permissions."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.lifecycle_catalog import LifecycleInputError
from app.models import AuditEvent, Customer, Device, LifecycleRecord, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Lifecycle-Catalog-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def code(response):
    assert response.status_code == 422, response.text
    return response.json()["detail"]["code"]


def main():
    suffix = uuid.uuid4().hex[:8].upper()
    today = utcnow().date()
    model = f"CI-LC-{suffix}"
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Catalog {suffix}", code=f"LC{suffix[:6]}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-LC-RTR-{suffix}", model=model, status="online")
        alias_device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-LC-ALIAS-{suffix}", model=f"{model}-ALT", status="online")
        db.add_all([device, alias_device])
        db.add_all([
            User(username=f"ci-lc-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-lc-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        ids = {"device": device.id, "alias": alias_device.id}

    assert str(LifecycleInputError("source", "La fonte è obbligatoria.")) == "La fonte è obbligatoria."
    base = "/api/ui/v1/lifecycle/catalog"
    assert TestClient(app).get(base).status_code == 401
    admin, csrf = login(f"ci-lc-{suffix}")
    auditor, auditor_csrf = login(f"ci-lc-aud-{suffix}")

    listing = auditor.get(base).json()
    assert listing["can_manage"] is False and listing["csv_url"].endswith(".csv") and "evidence_date" in listing["csv_fields"]
    assert auditor.get(f"{base}/form").status_code == 403
    form = admin.get(f"{base}/form").json()
    assert form["record"] is None and "mikrotik" in form["vendors"] and form["today"] == today.isoformat()

    body = {"vendor": "MikroTik", "model": model, "aliases": f"{model}-ALT", "eol_date": (today - timedelta(days=10)).isoformat(), "eos_date": "",
            "source": "TEST vendor notice", "source_url": "https://example.test/eol", "evidence_date": today.isoformat(), "notes": "TEST"}
    assert auditor.post(base, headers=auditor_csrf, json=body).status_code == 403
    assert admin.post(base, json=body).status_code == 403, "CSRF header required"
    assert code(admin.post(base, headers=csrf, json={**body, "model": " "})) == "required"
    assert code(admin.post(base, headers=csrf, json={**body, "source": ""})) == "source"
    assert code(admin.post(base, headers=csrf, json={**body, "evidence_date": ""})) == "evidence"
    assert code(admin.post(base, headers=csrf, json={**body, "evidence_date": (today + timedelta(days=3)).isoformat()})) == "evidence_future"
    assert code(admin.post(base, headers=csrf, json={**body, "eol_date": "31/12/2025"})) == "date_invalid"
    assert code(admin.post(base, headers=csrf, json={**body, "eos_date": (today - timedelta(days=400)).isoformat()})) == "eos_before_eol"
    assert code(admin.post(base, headers=csrf, json={**body, "source_url": "ftp://example.test"})) == "url"
    saved = admin.post(base, headers=csrf, json=body).json()
    assert saved["model"] == model and saved["linked"] == 2, saved
    with SessionLocal() as db:
        assert db.get(Device, ids["device"]).lifecycle_status == "eol" and db.get(Device, ids["alias"]).lifecycle_status == "eol"
        assert db.query(AuditEvent).filter(AuditEvent.event_type == "LIFECYCLE_RECORD_SAVED").count() >= 1
    assert code(admin.post(base, headers=csrf, json={**body, "model": f"{model}-ALT", "aliases": ""})) == "duplicate"

    rows = admin.get(f"{base}?vendor=mikrotik&q={model}").json()["items"]
    assert len(rows) == 1 and rows[0]["devices"] == 2 and rows[0]["status"] == "eol" and rows[0]["aliases"] == [f"{model}-ALT"]
    assert admin.get(f"{base}?q=does-not-exist-{suffix}").json()["items"] == []

    edit = admin.get(f"{base}/form?record={saved['id']}").json()
    assert edit["record"]["source_url"] == "https://example.test/eol"
    updated = admin.post(base, headers=csrf, json={**body, "record_id": saved["id"], "eol_date": "", "aliases": ""}).json()
    assert updated["id"] == saved["id"] and updated["linked"] == 1
    assert admin.get(f"{base}/form?record={uuid.uuid4()}").status_code == 404

    # CSV import: invalid rows import nothing, a valid file creates and updates.
    bad = "vendor,model,source,evidence_date\nmikrotik,,TEST,2026-01-01\n"
    assert code(admin.post(f"{base}/import", headers=csrf, files={"file": ("bad.csv", bad.encode(), "text/csv")})) == "import"
    assert code(admin.post(f"{base}/import", headers=csrf, files={"file": ("bin.csv", b"\xff\xfe\x00bad", "text/csv")})) == "encoding"
    good = (f"vendor,model,aliases,eol_date,eos_date,source,source_url,evidence_date,notes\n"
            f"mikrotik,{model},,,,TEST CSV,,{today.isoformat()},\n"
            f"ubiquiti,CI-UBNT-{suffix},,,,TEST CSV,,{today.isoformat()},\n")
    imported = admin.post(f"{base}/import", headers=csrf, files={"file": ("good.csv", good.encode(), "text/csv")}).json()
    assert imported["created"] == 1 and imported["updated"] == 1
    assert auditor.post(f"{base}/import", headers=auditor_csrf, files={"file": ("good.csv", good.encode(), "text/csv")}).status_code == 403

    assert admin.post(f"{base}/{saved['id']}/delete", headers=csrf).json()["deleted"] is True
    with SessionLocal() as db:
        assert db.get(LifecycleRecord, uuid.UUID(saved["id"])) is None and db.get(Device, ids["device"]).lifecycle_record_id is None
    assert admin.post(f"{base}/{saved['id']}/delete", headers=csrf).status_code == 404

    # The classic form keeps a readable flash (no "400:" prefix).
    page = admin.get("/security/lifecycle/catalog/new")
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    classic = admin.post("/security/lifecycle/catalog", data={"csrf": token, "vendor": "mikrotik", "model": "x", "source": ""}, follow_redirects=True)
    assert "La fonte è obbligatoria" in classic.text and "400:" not in classic.text

    for path in ("/app/lifecycle/catalog", f"/app/lifecycle/catalog/new?vendor=mikrotik&model={model}", f"/app/lifecycle/catalog/{uuid.uuid4()}/edit"):
        assert admin.get(path).status_code in (200, 503)
    print("ui lifecycle catalog smoke ok")


if __name__ == "__main__":
    main()
