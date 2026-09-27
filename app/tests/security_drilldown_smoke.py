import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, DeviceVulnerability, SecurityAdvisory, Site, User
from app.security import hash_password

PASSWORD = "Strong-CI12-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def seed():
    with SessionLocal() as db:
        for cve in ("CVE-2099-1201", "CVE-2099-1202"):
            old = db.scalar(select(SecurityAdvisory).where(SecurityAdvisory.cve_id == cve))
            if old:
                db.delete(old)
        for code in ("CI12A", "CI12B"):
            old = db.scalar(select(Customer).where(Customer.code == code))
            if old:
                db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci12admin"))
        if old_user:
            db.delete(old_user)
        db.commit()

        user = User(
            username="ci12admin",
            password_hash=hash_password(PASSWORD),
            display_name="CI12 Admin",
            role="admin",
            is_active=True,
        )
        a = Customer(name="CI12 Cliente A", code="CI12A")
        b = Customer(name="CI12 Cliente B", code="CI12B")
        db.add_all([user, a, b])
        db.flush()

        site_a = Site(customer_id=a.id, name="Sede principale")
        site_b = Site(customer_id=b.id, name="Sede principale")
        db.add_all([site_a, site_b])
        db.flush()

        device_a = Device(
            customer_id=a.id,
            site_id=site_a.id,
            vendor="mikrotik",
            device_type="router",
            name="CI12 Router A",
            display_name="CI12 Router A",
            device_identity="CI12-A",
            firmware_version="7.20.0",
            status="online",
        )
        device_b = Device(
            customer_id=b.id,
            site_id=site_b.id,
            vendor="mikrotik",
            device_type="router",
            name="CI12 Router B",
            display_name="CI12 Router B",
            device_identity="CI12-B",
            firmware_version="7.19.0",
            status="online",
        )
        db.add_all([device_a, device_b])
        db.flush()

        mixed = SecurityAdvisory(
            cve_id="CVE-2099-1201",
            vendor="mikrotik",
            product="RouterOS",
            severity="critical",
            cvss=9.8,
            source="ci",
            summary="CI12 mixed open/resolved advisory",
        )
        resolved_only = SecurityAdvisory(
            cve_id="CVE-2099-1202",
            vendor="ubiquiti",
            product="airOS",
            severity="high",
            cvss=8.1,
            source="ci",
            summary="CI12 resolved-only advisory",
        )
        db.add_all([mixed, resolved_only])
        db.flush()

        db.add_all(
            [
                DeviceVulnerability(
                    advisory_id=mixed.id,
                    device_id=device_a.id,
                    status="open",
                    installed_version="7.20.0",
                    fixed_version="7.21.0",
                ),
                DeviceVulnerability(
                    advisory_id=mixed.id,
                    device_id=device_b.id,
                    status="resolved",
                    installed_version="7.19.0",
                    fixed_version="7.21.0",
                ),
                DeviceVulnerability(
                    advisory_id=resolved_only.id,
                    device_id=device_b.id,
                    status="resolved",
                    installed_version="8.7.0",
                    fixed_version="8.7.1",
                ),
            ]
        )
        db.commit()
        return {
            "customer_a": a.id,
            "customer_b": b.id,
            "device_a": device_a.id,
            "device_b": device_b.id,
            "mixed": mixed.id,
            "resolved_only": resolved_only.id,
        }


def main():
    ids = seed()
    client = TestClient(app)

    login = client.get("/login")
    csrf = csrf_from(login.text)
    response = client.post(
        "/login",
        data={"username": "ci12admin", "password": PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get("/security/vulnerabilities")
    assert page.status_code == 200
    assert "CVE-2099-1201" in page.text
    assert "CVE-2099-1202" not in page.text
    assert "Tutti i clienti" in page.text
    assert "Apparati" in page.text and "Clienti" in page.text

    customer_a = client.get(
        f"/security/vulnerabilities?customer={ids['customer_a']}&status=open"
    )
    assert customer_a.status_code == 200
    assert "CI12 Cliente A" in customer_a.text
    assert "Vista Security confinata al cliente selezionato" in customer_a.text
    assert "CVE-2099-1201" in customer_a.text
    assert "CVE-2099-1202" not in customer_a.text

    customer_b_resolved = client.get(
        f"/security/vulnerabilities?customer={ids['customer_b']}&status=resolved"
    )
    assert customer_b_resolved.status_code == 200
    assert "CVE-2099-1201" in customer_b_resolved.text
    assert "CVE-2099-1202" in customer_b_resolved.text

    vendor_filter = client.get("/security/vulnerabilities?vendor=ubiquiti&status=resolved")
    assert vendor_filter.status_code == 200
    assert "CVE-2099-1202" in vendor_filter.text
    assert "CVE-2099-1201" not in vendor_filter.text

    detail_a = client.get(
        f"/security/vulnerabilities/{ids['mixed']}?customer={ids['customer_a']}&status=open"
    )
    assert detail_a.status_code == 200
    assert "CI12 Router A" in detail_a.text
    assert "CI12 Router B" not in detail_a.text
    assert "Impatto CVE limitato agli apparati di questo cliente" in detail_a.text

    detail_b = client.get(
        f"/security/vulnerabilities/{ids['mixed']}?customer={ids['customer_b']}&status=resolved"
    )
    assert detail_b.status_code == 200
    assert "CI12 Router B" in detail_b.text
    assert "CI12 Router A" not in detail_b.text

    assert str(app.url_path_for("vulnerabilities")) == "/security/vulnerabilities"
    assert str(app.url_path_for("vulnerability_detail", advisory_id=ids["mixed"])) == f"/security/vulnerabilities/{ids['mixed']}"

    print("Core 0.12 customer-aware security drilldown smoke test passed")


if __name__ == "__main__":
    main()
