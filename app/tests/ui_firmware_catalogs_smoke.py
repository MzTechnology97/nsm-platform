"""New interface, vendor firmware catalogs: catalogs per brand with icons, add/delete operator releases with coded checks, refresh, permissions."""
import re
import uuid

from fastapi.testclient import TestClient

from app import vendor_firmware
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User
from app.security import hash_password
from app.vendor_firmware_models import VendorFirmwareRelease

PASSWORD = "CI-UI-Firmware-Catalogs-2026"


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
        customer = Customer(name=f"CI UI Catalogs {suffix}", code=f"FC{suffix}")
        db.add(customer)
        db.flush()
        archer = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-FC-TPL-{suffix}", model="Archer C6",
                        firmware_version="1.1.0", status="online")
        db.add(archer)
        db.add_all([
            User(username=f"ci-fc-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-fc-a-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        archer_id = archer.id

    base = "/api/ui/v1/firmware/catalogs"
    assert TestClient(app).get(base).status_code == 401
    admin, csrf = login(f"ci-fc-{suffix}")
    auditor, auditor_csrf = login(f"ci-fc-a-{suffix}")

    data = auditor.get(base).json()
    tplink = next(c for c in data["catalogs"] if c["brand"] == "tp-link")
    assert data["can_execute"] is False and tplink["icon"]["symbol"] == "brand-tp-link" and tplink["devices"] >= 1 and tplink["page"].startswith("https://")
    assert all(c["brand"] != "mikrotik" for c in data["catalogs"])

    add = f"{base}/releases"
    body = {"brand": "tp-link", "version": "v1.2.0", "model_pattern": "archer c6*", "released_at": "2026-09-01",
            "notes_url": "https://example.test/notes", "security": True, "notes": "TEST fix"}
    assert auditor.post(add, headers=auditor_csrf, json=body).status_code == 403
    assert admin.post(add, json=body).status_code == 403, "CSRF header required"
    assert code(admin.post(add, headers=csrf, json={**body, "brand": "acme"})) == "brand"
    assert code(admin.post(add, headers=csrf, json={**body, "version": "latest"})) == "version"
    assert code(admin.post(add, headers=csrf, json={**body, "notes_url": "ftp://example.test"})) == "url"
    assert code(admin.post(add, headers=csrf, json={**body, "released_at": "01/09/2026"})) == "date"
    added = admin.post(add, headers=csrf, json=body).json()
    assert added["version"] == "1.2.0" and added["to_update"] >= 1
    assert code(admin.post(add, headers=csrf, json=body)) == "exists"
    with SessionLocal() as db:
        device = db.get(Device, archer_id)
        assert device.firmware_status == "security_update" and device.recommended_firmware_version == "1.2.0"
        release = db.query(VendorFirmwareRelease).filter_by(brand="tp-link", version="1.2.0", source="operator").one()
        release_id = str(release.id)
        assert db.query(AuditEvent).filter(AuditEvent.event_type == "VENDOR_FIRMWARE_RELEASE_ADDED").count() >= 1
        other = VendorFirmwareRelease(brand="tp-link", version="9.9.9", source="vendor_api", model_pattern="", platform="")
        db.add(other)
        db.commit()
        other_id = str(other.id)

    row = next(r for r in next(c for c in admin.get(base).json()["catalogs"] if c["brand"] == "tp-link")["releases"] if r["id"] == release_id)
    assert row["model_pattern"] == "archer c6*" and row["security"] is True and row["source"] == "operator" and row["released_at"].startswith("2026-09-01")

    assert code(admin.post(f"{base}/releases/{other_id}/delete", headers=csrf)) == "not_operator"
    assert admin.post(f"{base}/releases/{release_id}/delete", headers=csrf).json() == {"deleted": True}
    with SessionLocal() as db:
        assert db.get(VendorFirmwareRelease, uuid.UUID(release_id)) is None
        db.delete(db.get(VendorFirmwareRelease, uuid.UUID(other_id)))
        db.commit()

    # Refresh without network: the official update service is replaced for the test.
    vendor_firmware.refresh_vendor_api = lambda db, now=None, transport=None: {"created": 0, "platforms": 0, "errors": ["fw-update.example.test"]}
    assert auditor.post(f"{base}/refresh", headers=auditor_csrf).status_code == 403
    refreshed = admin.post(f"{base}/refresh", headers=csrf).json()
    assert refreshed["errors"] == ["fw-update.example.test"] and refreshed["evaluated"] >= 0

    assert admin.get("/app/firmware/catalogs").status_code in (200, 503)
    print("ui firmware catalogs smoke ok")


if __name__ == "__main__":
    main()
