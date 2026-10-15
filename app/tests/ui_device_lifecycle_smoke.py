"""New interface, device lifecycle: manual value with its checks, back to the catalog, end-of-life decisions with history, permissions."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Device-Lifecycle-2026"


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
    today = utcnow().date()
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Device Lifecycle {suffix}", code=f"DL{suffix}")
        db.add(customer)
        db.flush()
        old = Device(customer_id=customer.id, vendor="generic", device_type="switch", name=f"TEST-DL-OLD-{suffix}", display_name=f"TEST-DL-OLD-{suffix}",
                     model=f"CI-OLD-{suffix}", status="online")
        new = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DL-NEW-{suffix}", display_name=f"TEST-DL-NEW-{suffix}", status="online")
        db.add_all([old, new])
        db.add_all([
            User(username=f"ci-dl-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-dl-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        ids = {"old": str(old.id), "new": str(new.id)}

    admin, admin_csrf = login(f"ci-dl-{suffix}")
    auditor, auditor_csrf = login(f"ci-dl-aud-{suffix}")
    base = f"/api/ui/v1/devices/{ids['old']}/lifecycle"
    assert TestClient(app).get(base).status_code == 401

    tab = auditor.get(base).json()
    assert tab["correlation"]["match"] == "no_record" and tab["remediation"] is None and tab["can_manage"] is False
    assert "eol" in tab["states"]
    assert any(c["id"] == ids["new"] and c["brand"]["symbol"] == "brand-mikrotik" for c in tab["replacement_candidates"])

    # Manual value: lifecycle.manage, CSRF, the classic checks as codes.
    good = {"lifecycle_status": "eol", "source": "CI vendor notice", "evidence_date": today.isoformat(), "eol_date": "2025-01-31", "eos_date": "2026-01-31"}
    assert auditor.post(base, headers=auditor_csrf, json=good).status_code == 403
    assert admin.post(base, json=good).status_code == 403, "CSRF header required"
    assert code(admin.post(base, headers=admin_csrf, json={**good, "lifecycle_status": "zombie"})) == "status"
    assert code(admin.post(base, headers=admin_csrf, json={**good, "source": " "})) == "source"
    assert code(admin.post(base, headers=admin_csrf, json={**good, "evidence_date": (today + timedelta(days=2)).isoformat()})) == "evidence"
    assert code(admin.post(base, headers=admin_csrf, json={**good, "eol_date": "31/01/2025"})) == "date_invalid"
    assert code(admin.post(base, headers=admin_csrf, json={**good, "eos_date": "2024-01-01"})) == "eos_before_eol"
    assert admin.post(base, headers=admin_csrf, json=good).json() == {"status": "eol"}
    tab = admin.get(base).json()
    assert tab["device"]["match"] == "manual" and tab["device"]["eol_date"] == "2025-01-31" and tab["remediation"]["status"] == "open"

    # Decisions on the EOL device.
    rem = f"{base}/remediation"
    assert code(admin.post(rem, headers=admin_csrf, json={"action": "plan", "target_date": (today - timedelta(days=1)).isoformat(), "note": "swap it"})) == "invalid"
    assert admin.post(rem, headers=admin_csrf, json={"action": "plan", "target_date": (today + timedelta(days=30)).isoformat(), "note": "swap in Q4"}).json()["status"] == "planned"
    assert code(admin.post(rem, headers=admin_csrf, json={"action": "exception", "exception_until": (today + timedelta(days=30)).isoformat(), "note": "short"})) == "invalid"
    assert admin.post(rem, headers=admin_csrf, json={"action": "exception", "exception_until": (today + timedelta(days=30)).isoformat(),
                                                     "note": "isolated segment, no WAN"}).json()["status"] == "exception"
    assert code(admin.post(rem, headers=admin_csrf, json={"action": "replaced", "replacement_device_id": ids["old"]})) == "invalid"
    assert admin.post(rem, headers=admin_csrf, json={"action": "replaced", "replacement_device_id": ids["new"], "note": ""}).json()["status"] == "replaced"
    tab = admin.get(base).json()
    assert tab["remediation"]["replacement"]["id"] == ids["new"] and len(tab["history"]) >= 3
    assert admin.post(rem, headers=admin_csrf, json={"action": "reopen"}).json()["status"] == "open"
    assert code(admin.post(rem, headers=admin_csrf, json={"action": "explode"})) == "invalid"
    assert auditor.post(rem, headers=auditor_csrf, json={"action": "reopen"}).status_code == 403

    # Back to the catalog (no record for this model): the value is unknown again and nothing is left to handle.
    assert admin.post(f"{base}/catalog", headers=admin_csrf).json()["match"] == "no_record"
    after = admin.get(base).json()["device"]
    assert after["match"] == "no_record" and after["status"] != "eol" and after["source"] is None

    # Classic form: the flash shows the message alone, not "400: ...".
    from app.lifecycle_catalog import LifecycleInputError
    assert str(LifecycleInputError("status", "Seleziona lo stato lifecycle.")) == "Seleziona lo stato lifecycle."

    assert admin.get(f"/app/devices/{ids['old']}/lifecycle").status_code in (200, 503)
    print("ui device lifecycle smoke ok")


if __name__ == "__main__":
    main()
