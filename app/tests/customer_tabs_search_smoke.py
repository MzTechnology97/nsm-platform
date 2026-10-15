import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, DeviceVulnerability, SecurityAdvisory, Site, User
from app.security import hash_password

PASSWORD = "Strong-CI15-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def seed():
    with SessionLocal() as db:
        old = db.scalar(select(Customer).where(Customer.code == "CI15"))
        if old:
            db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci15admin"))
        if old_user:
            db.delete(old_user)
        old_adv = db.scalar(select(SecurityAdvisory).where(SecurityAdvisory.cve_id == "CVE-2099-1515"))
        if old_adv:
            db.delete(old_adv)
        db.commit()

        user = User(username="ci15admin", password_hash=hash_password(PASSWORD), display_name="CI15 Admin", role="admin", is_active=True)
        customer = Customer(name="CI15 Cliente", code="CI15")
        db.add_all([user, customer])
        db.flush()
        site = Site(customer_id=customer.id, name="CI15 POP Centro", address="Via Test 15")
        db.add(site)
        db.flush()
        device = Device(
            customer_id=customer.id,
            site_id=site.id,
            vendor="mikrotik",
            device_type="router",
            name="CI15 Router",
            display_name="Core CI15",
            device_identity="CI15-CCR",
            model="CCR2004-1G-12S+2XS",
            serial_number="CI15SERIAL98765",
            primary_mac="02:15:AA:BB:CC:DD",
            management_ip="192.0.2.115",
            firmware_version="7.20.2",
            recommended_firmware_version="7.21.1",
            firmware_status="outdated",
            lifecycle_status="supported",
            status="online",
        )
        db.add(device)
        db.flush()
        advisory = SecurityAdvisory(cve_id="CVE-2099-1515", vendor="mikrotik", product="RouterOS", severity="critical", cvss=9.5, source="ci")
        db.add(advisory)
        db.flush()
        db.add(DeviceVulnerability(advisory_id=advisory.id, device_id=device.id, status="open", installed_version="7.20.2", fixed_version="7.21.1"))
        db.add(AuditEvent(event_type="CI15_TEST_EVENT", severity="info", customer_id=customer.id, device_id=device.id, actor_user_id=user.id, source="ci", result="success", details={"serial": device.serial_number}))
        db.commit()
        return customer.id, site.id, device.id


def login(client):
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post("/login", data={"username": "ci15admin", "password": PASSWORD, "csrf": csrf}, follow_redirects=False)
    assert response.status_code == 303


def assert_device_match(client, query, expected_field):
    response = client.get("/api/v1/search/suggest", params={"q": query})
    assert response.status_code == 200
    rows = [row for row in response.json()["results"] if row["type"] == "Apparato"]
    assert rows, response.json()
    row = rows[0]
    assert row["match_field"] == expected_field, row
    assert row["serial"] == "CI15SERIAL98765"
    assert row["mac"] == "02:15:AA:BB:CC:DD"
    assert row["ip"] == "192.0.2.115"
    assert row["completion"]


def main():
    customer_id, site_id, device_id = seed()
    client = TestClient(app)
    login(client)

    overview = client.get(f"/api/ui/v1/customers/{customer_id}")
    assert overview.status_code == 200 and "CI15 POP Centro" in overview.text

    devices = client.get("/api/ui/v1/devices", params={"customer": str(customer_id)})
    assert devices.status_code == 200
    assert "CI15SERIAL98765" in devices.text
    assert "02:15:AA:BB:CC:DD" in devices.text
    assert "192.0.2.115" in devices.text
    assert "CCR2004-1G-12S+2XS" in devices.text

    site_devices = client.get("/api/ui/v1/devices", params={"customer": str(customer_id), "site": str(site_id)})
    assert site_devices.status_code == 200 and "CI15SERIAL98765" in site_devices.text

    security = client.get(f"/api/ui/v1/customers/{customer_id}/security")
    assert security.status_code == 200
    assert "CVE-2099-1515" in security.text
    assert "7.21.1" in security.text

    history = client.get("/api/ui/v1/audit/events", params={"customer": str(customer_id)})
    assert history.status_code == 200
    assert "CI15_TEST_EVENT" in history.text
    assert "ci15admin" in history.text

    global_backup = client.get("/api/ui/v1/backups")
    assert global_backup.status_code == 200, global_backup.text
    assert {"attention", "no_policy", "all"} <= set(global_backup.json()["counts"]), "per-device worklist with quick filters"

    assert_device_match(client, "CI15SERIAL", "Seriale")
    assert_device_match(client, "AA:BB:CC", "MAC")
    assert_device_match(client, "192.0.2.115", "IP")

    full_search = client.get("/api/ui/v1/search", params={"q": "CI15SERIAL"})
    assert full_search.status_code == 200
    device = full_search.json()["devices"][0]
    assert device["serial_number"] == "CI15SERIAL98765" and device["primary_mac"] == "02:15:AA:BB:CC:DD" and device["management_ip"] == "192.0.2.115"

    assert str(app.url_path_for("search_suggest")) == "/api/v1/search/suggest"

    print("Core 0.15 customer tabs and enhanced search smoke test passed")


if __name__ == "__main__":
    main()
