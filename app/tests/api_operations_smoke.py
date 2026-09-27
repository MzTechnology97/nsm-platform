import hashlib
import re
import secrets

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api_key_models import PlatformApiKey
from app.db import SessionLocal
from app.entrypoint import app
from app.models import (
    BackupRun,
    Customer,
    Device,
    DeviceVulnerability,
    SecurityAdvisory,
    User,
    utcnow,
)
from app.security import hash_password

PASSWORD = "Strong-CI24-Password-2026"
ALL_SCOPES = ["inventory.read", "backup.read", "security.read", "firmware.read"]


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token missing"
    return match.group(1)


def login(client):
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post(
        "/login",
        data={"username": "ci24admin", "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def token_value():
    return "nsm_live_" + secrets.token_urlsafe(32)


def add_key(db, name, scopes, customer_id=None):
    raw = token_value()
    row = PlatformApiKey(
        name=name,
        key_prefix=raw[:20],
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        scopes=scopes,
        customer_id=customer_id,
        is_active=True,
    )
    db.add(row)
    return raw


def seed():
    with SessionLocal() as db:
        for code in ("CI24A", "CI24B"):
            old = db.scalar(select(Customer).where(Customer.code == code))
            if old:
                db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci24admin"))
        if old_user:
            db.delete(old_user)
        old_advisory = db.scalar(select(SecurityAdvisory).where(SecurityAdvisory.cve_id == "CVE-2026-24001"))
        if old_advisory:
            db.delete(old_advisory)
        db.commit()

        admin = User(
            username="ci24admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI24 Admin",
            role="admin",
            is_active=True,
        )
        customer_a = Customer(name="CI24 Customer A", code="CI24A")
        customer_b = Customer(name="CI24 Customer B", code="CI24B")
        db.add_all([admin, customer_a, customer_b])
        db.flush()

        device_a = Device(
            customer_id=customer_a.id,
            vendor="mikrotik",
            device_type="router",
            name="CI24-A-Router",
            display_name="CI24 A Router",
            model="CCR2004-1G-12S+2XS",
            firmware_version="7.20.1",
            firmware_status="update_available",
            recommended_firmware_version="7.20.2",
            lifecycle_status="supported",
            status="online",
        )
        device_b = Device(
            customer_id=customer_b.id,
            vendor="ubiquiti",
            device_type="cpe",
            name="CI24-B-Radio",
            display_name="CI24 B Radio",
            model="PowerBeam 5AC",
            firmware_version="8.7.19",
            firmware_status="current",
            recommended_firmware_version="8.7.19",
            lifecycle_status="supported",
            status="online",
        )
        db.add_all([device_a, device_b])
        db.flush()

        run_a = BackupRun(
            device_id=device_a.id,
            status="success",
            backup_type="mikrotik_agent",
            started_at=utcnow(),
            completed_at=utcnow(),
            size_bytes=4096,
            sha256="a" * 64,
            file_path="/should/not/be/exposed/a.backup",
        )
        run_b = BackupRun(
            device_id=device_b.id,
            status="failed",
            backup_type="uisp",
            started_at=utcnow(),
            completed_at=utcnow(),
            error_message="CI24 simulated backup failure",
            file_path="/should/not/be/exposed/b.cfg",
        )
        advisory = SecurityAdvisory(
            cve_id="CVE-2026-24001",
            vendor="mikrotik",
            product="RouterOS",
            severity="critical",
            cvss=9.8,
            published_at=utcnow(),
            summary="CI24 test advisory",
            vendor_advisory_url="https://example.invalid/vendor/CVE-2026-24001",
            nvd_url="https://example.invalid/nvd/CVE-2026-24001",
            source="ci24",
        )
        db.add_all([run_a, run_b, advisory])
        db.flush()
        db.add_all(
            [
                DeviceVulnerability(
                    advisory_id=advisory.id,
                    device_id=device_a.id,
                    status="open",
                    installed_version="7.20.1",
                    fixed_version="7.20.2",
                    evidence={"internal": "must-not-leak"},
                ),
                DeviceVulnerability(
                    advisory_id=advisory.id,
                    device_id=device_b.id,
                    status="resolved",
                    installed_version="8.7.18",
                    fixed_version="8.7.19",
                    evidence={"internal": "must-not-leak"},
                ),
            ]
        )
        inventory_token = add_key(db, "CI24 Inventory Only", ["inventory.read"])
        scoped_token = add_key(db, "CI24 Customer A Ops", ALL_SCOPES, customer_a.id)
        db.commit()
        return customer_a.id, customer_b.id, device_a.id, device_b.id, inventory_token, scoped_token


def create_global_operations_key(client):
    page = client.get("/admin/api-keys")
    assert page.status_code == 200
    for marker in ("backup.read", "security.read", "firmware.read"):
        assert marker in page.text
    csrf = csrf_from(page.text)
    response = client.post(
        "/admin/api-keys",
        data={
            "csrf": csrf,
            "name": "CI24 Global Ops",
            "customer_id": "",
            "expires_days": "90",
            "scopes": ["backup.read", "security.read", "firmware.read", "not.allowed"],
        },
    )
    assert response.status_code == 200, response.text
    match = re.search(r"(nsm_live_[A-Za-z0-9_-]+)", response.text)
    assert match
    token = match.group(1)
    with SessionLocal() as db:
        row = db.scalar(select(PlatformApiKey).where(PlatformApiKey.name == "CI24 Global Ops"))
        assert row
        assert row.scopes == ALL_SCOPES
    return token


def main():
    customer_a, customer_b, device_a, device_b, inventory_token, scoped_token = seed()
    client = TestClient(app)
    login(client)
    global_token = create_global_operations_key(client)

    inventory_denied = client.get(
        "/api/v1/public/backups",
        headers={"X-API-KEY": inventory_token},
    )
    assert inventory_denied.status_code == 403

    backups = client.get(
        "/api/v1/public/backups?limit=1000",
        headers={"X-API-KEY": global_token},
    )
    assert backups.status_code == 200, backups.text
    assert backups.json()["limit"] == 100
    backup_items = [item for item in backups.json()["items"] if item["device_id"] in {str(device_a), str(device_b)}]
    assert {item["device_id"] for item in backup_items} == {str(device_a), str(device_b)}
    assert all("file_path" not in item for item in backup_items)

    security = client.get(
        "/api/v1/public/security/vulnerabilities?cve=CVE-2026-24001",
        headers={"Authorization": f"Bearer {global_token}"},
    )
    assert security.status_code == 200, security.text
    security_items = security.json()["items"]
    assert {item["device_id"] for item in security_items} == {str(device_a), str(device_b)}
    assert all("evidence" not in item for item in security_items)

    critical = client.get(
        "/api/v1/public/security/vulnerabilities?severity=critical&status=open",
        headers={"X-API-KEY": global_token},
    )
    assert critical.status_code == 200
    assert any(item["device_id"] == str(device_a) for item in critical.json()["items"])
    assert all(item["status"] == "open" for item in critical.json()["items"])

    firmware = client.get(
        "/api/v1/public/firmware?vendor=mikrotik&status=update_available",
        headers={"X-API-KEY": global_token},
    )
    assert firmware.status_code == 200, firmware.text
    assert any(item["device_id"] == str(device_a) for item in firmware.json()["items"])
    assert all(item["vendor"] == "mikrotik" for item in firmware.json()["items"])

    scoped_backups = client.get(
        "/api/v1/public/backups",
        headers={"X-API-KEY": scoped_token},
    )
    assert scoped_backups.status_code == 200
    scoped_backup_ids = {item["device_id"] for item in scoped_backups.json()["items"]}
    assert str(device_a) in scoped_backup_ids and str(device_b) not in scoped_backup_ids

    scoped_security = client.get(
        "/api/v1/public/security/vulnerabilities?cve=CVE-2026-24001",
        headers={"X-API-KEY": scoped_token},
    )
    assert scoped_security.status_code == 200
    scoped_security_ids = {item["device_id"] for item in scoped_security.json()["items"]}
    assert scoped_security_ids == {str(device_a)}

    scoped_firmware = client.get(
        "/api/v1/public/firmware",
        headers={"X-API-KEY": scoped_token},
    )
    assert scoped_firmware.status_code == 200
    scoped_firmware_ids = {item["device_id"] for item in scoped_firmware.json()["items"]}
    assert str(device_a) in scoped_firmware_ids and str(device_b) not in scoped_firmware_ids

    for endpoint in (
        "/api/v1/public/backups",
        "/api/v1/public/security/vulnerabilities",
        "/api/v1/public/firmware",
    ):
        forbidden = client.get(
            f"{endpoint}?customer_id={customer_b}",
            headers={"X-API-KEY": scoped_token},
        )
        assert forbidden.status_code == 403, (endpoint, forbidden.text)

    no_api_key = client.get("/api/v1/public/firmware")
    assert no_api_key.status_code == 401

    print("Core 0.24 scoped read-only operations API smoke test passed")


if __name__ == "__main__":
    main()
