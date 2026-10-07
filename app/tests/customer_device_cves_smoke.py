"""Customer device list shows open CVEs per device and links to a device-filtered view."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, DeviceVulnerability, SecurityAdvisory, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Customer-CVEs-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:8]
    number = int(uuid.uuid4().int % 90000) + 10000
    now = utcnow()
    with SessionLocal() as db:
        tech = User(username=f"ci-cc-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        customer = Customer(name=f"CI CVEs {suffix}", code=f"CV{suffix[:6]}")
        db.add_all([tech, customer])
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-CC-RTR", status="online", firmware_version="7.12.1")
        clean = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-CC-CLEAN", status="online", firmware_version="7.24.4")
        cpe = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name="TEST-CC-CPE", status="online")
        critical = SecurityAdvisory(cve_id=f"CVE-2099-{number}", source="manual", severity="critical", cvss=9.8)
        medium = SecurityAdvisory(cve_id=f"CVE-2098-{number}", source="manual", severity="medium", cvss=5.0)
        fixed = SecurityAdvisory(cve_id=f"CVE-2097-{number}", source="manual", severity="high", cvss=7.5)
        db.add_all([router, clean, cpe, critical, medium, fixed])
        db.flush()
        db.add_all([
            DeviceVulnerability(advisory_id=critical.id, device_id=router.id, status="open", detected_at=now),
            DeviceVulnerability(advisory_id=medium.id, device_id=router.id, status="planned", detected_at=now),
            DeviceVulnerability(advisory_id=fixed.id, device_id=router.id, status="resolved", detected_at=now, resolved_at=now),
        ])
        db.commit()
        ids = {"customer": customer.id, "router": router.id}

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-cc-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/customers/{ids['customer']}/devices").text
    assert "<th>CVE</th>" in page
    link = f'href="/security/vulnerabilities?customer={ids["customer"]}&device={ids["router"]}&status=open"'
    assert link in page and "2 · CRITICAL" in page and "1 da gestire" in page, "resolved findings are not counted"
    row_clean = page[page.index("TEST-CC-CLEAN"):page.index("TEST-CC-CLEAN") + 1500]
    assert '<span class="muted">0</span>' in row_clean
    row_cpe = page[page.index("TEST-CC-CPE"):page.index("TEST-CC-CPE") + 1500]
    assert "Nessun dato CVE" in row_cpe

    filtered = client.get(f"/security/vulnerabilities?customer={ids['customer']}&device={ids['router']}&status=open").text
    assert "CVE che interessano solo questo apparato" in filtered
    assert f"CVE-2099-{number}" in filtered and f"CVE-2098-{number}" in filtered and f"CVE-2097-{number}" not in filtered
    print("Customer device CVE column smoke passed")


if __name__ == "__main__":
    main()
