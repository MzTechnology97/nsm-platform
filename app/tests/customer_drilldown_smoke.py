import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import (
    ActionIssue,
    Customer,
    Device,
    DeviceVulnerability,
    SecurityAdvisory,
    Site,
    User,
)
from app.security import hash_password

PASSWORD = "Strong-CI11-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def seed():
    with SessionLocal() as db:
        for code in ("CI11A", "CI11B"):
            old = db.scalar(select(Customer).where(Customer.code == code))
            if old:
                db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci11admin"))
        if old_user:
            db.delete(old_user)
        old_adv = db.scalar(
            select(SecurityAdvisory).where(SecurityAdvisory.cve_id == "CVE-2099-1111")
        )
        if old_adv:
            db.delete(old_adv)
        db.commit()

        user = User(
            username="ci11admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI11 Admin",
            role="admin",
            is_active=True,
        )
        customer_a = Customer(name="CI11 Cliente A", code="CI11A")
        customer_b = Customer(name="CI11 Cliente B", code="CI11B")
        db.add_all([user, customer_a, customer_b])
        db.flush()

        site_a = Site(customer_id=customer_a.id, name="Sede principale")
        site_b = Site(customer_id=customer_b.id, name="Sede principale")
        db.add_all([site_a, site_b])
        db.flush()

        device_update = Device(
            customer_id=customer_a.id,
            site_id=site_a.id,
            vendor="mikrotik",
            device_type="router",
            name="CI11 Update Router",
            display_name="CI11 Update Router",
            firmware_version="7.20.2",
            recommended_firmware_version="7.21.1",
            firmware_status="outdated",
            status="online",
        )
        device_cve = Device(
            customer_id=customer_a.id,
            site_id=site_a.id,
            vendor="ubiquiti",
            device_type="wireless_ap",
            name="CI11 CVE Radio",
            display_name="CI11 CVE Radio",
            firmware_version="8.7.0",
            firmware_status="current",
            status="online",
        )
        device_b = Device(
            customer_id=customer_b.id,
            site_id=site_b.id,
            vendor="mikrotik",
            device_type="router",
            name="CI11 Other Customer Router",
            display_name="CI11 Other Customer Router",
            firmware_version="7.21.1",
            firmware_status="current",
            status="online",
        )
        db.add_all([device_update, device_cve, device_b])
        db.flush()

        advisory = SecurityAdvisory(
            cve_id="CVE-2099-1111",
            vendor="ubiquiti",
            product="AirOS",
            severity="critical",
            cvss=9.8,
            source="ci",
        )
        db.add(advisory)
        db.flush()
        # Two rows on one device verify that the device drilldown does not duplicate it.
        db.add_all(
            [
                DeviceVulnerability(
                    advisory_id=advisory.id,
                    device_id=device_cve.id,
                    status="open",
                    installed_version="8.7.0",
                    fixed_version="8.7.1",
                ),
                DeviceVulnerability(
                    advisory_id=advisory.id,
                    device_id=device_cve.id,
                    status="acknowledged",
                    installed_version="8.7.0",
                    fixed_version="8.7.1",
                ),
            ]
        )
        db.add_all(
            [
                ActionIssue(
                    category="backup",
                    severity="high",
                    status="open",
                    title="CI11 Cliente A backup alert",
                    customer_id=customer_a.id,
                    device_id=device_update.id,
                    details={"source": "ci"},
                ),
                ActionIssue(
                    category="monitoring",
                    severity="critical",
                    status="open",
                    title="CI11 Cliente B monitoring alert",
                    customer_id=customer_b.id,
                    device_id=device_b.id,
                    details={"source": "ci"},
                ),
            ]
        )
        db.commit()
        return {
            "customer_a": customer_a.id,
            "customer_b": customer_b.id,
            "update": device_update.id,
            "cve": device_cve.id,
            "other": device_b.id,
        }


def main():
    ids = seed()
    client = TestClient(app)
    login = client.get("/login")
    csrf = csrf_from(login.text)
    response = client.post(
        "/login",
        data={"username": "ci11admin", "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    customers = client.get("/customers")
    assert customers.status_code == 200
    assert f"/devices?customer={ids['customer_a']}" in customers.text
    assert f"customer={ids['customer_a']}&amp;firmware=attention" in customers.text
    assert f"customer={ids['customer_a']}&amp;security=severe" in customers.text
    assert f"/action-center?customer={ids['customer_a']}" in customers.text

    profile = client.get(f"/customers/{ids['customer_a']}")
    assert profile.status_code == 200
    assert f"/devices?customer={ids['customer_a']}&amp;firmware=attention" in profile.text
    assert f"/devices?customer={ids['customer_a']}&amp;security=severe" in profile.text
    assert f"/action-center?customer={ids['customer_a']}" in profile.text

    all_a = client.get(f"/devices?customer={ids['customer_a']}")
    assert all_a.status_code == 200
    assert "CI11 Update Router" in all_a.text
    assert "CI11 CVE Radio" in all_a.text
    assert "CI11 Other Customer Router" not in all_a.text

    firmware = client.get(
        f"/devices?customer={ids['customer_a']}&firmware=attention"
    )
    assert firmware.status_code == 200
    assert "CI11 Update Router" in firmware.text
    assert "CI11 CVE Radio" not in firmware.text
    assert "CI11 Other Customer Router" not in firmware.text
    assert "7.21.1" in firmware.text

    cve = client.get(f"/devices?customer={ids['customer_a']}&security=severe")
    assert cve.status_code == 200
    assert cve.text.count("CI11 CVE Radio") >= 1
    assert "2 CVE gravi" in cve.text
    assert "CI11 Update Router" not in cve.text
    assert "CI11 Other Customer Router" not in cve.text

    action_a = client.get(f"/action-center?customer={ids['customer_a']}")
    assert action_a.status_code == 200
    assert "CI11 Cliente A backup alert" in action_a.text
    assert "CI11 Cliente B monitoring alert" not in action_a.text
    assert "1 elementi" in action_a.text

    action_b = client.get(f"/action-center?customer={ids['customer_b']}")
    assert action_b.status_code == 200
    assert "CI11 Cliente B monitoring alert" in action_b.text
    assert "CI11 Cliente A backup alert" not in action_b.text

    assert str(app.url_path_for("devices")) == "/devices"
    assert str(app.url_path_for("action_center")) == "/action-center"

    print("Core 0.11 customer drilldown smoke test passed")


if __name__ == "__main__":
    main()
