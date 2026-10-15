"""New interface, customer Security tab: severe open CVEs with device icon, firmware, end of life, issues, compliance, scope."""
import re
import uuid
from datetime import date

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, Customer, Device, DeviceVulnerability, SecurityAdvisory, User
from app.security import hash_password

PASSWORD = "CI-UI-Customer-Security-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Security {suffix}", code=f"US{suffix}")
        other = Customer(name=f"CI UI Security other {suffix}", code=f"UT{suffix}")
        db.add_all([customer, other])
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-US-MT-{suffix}", display_name=f"TEST-US-MT-{suffix}",
                        status="online", firmware_version="7.10", recommended_firmware_version="7.19.4", firmware_status="outdated")
        radio = Device(customer_id=customer.id, vendor="ubiquiti", device_type="radio", name=f"TEST-US-UB-{suffix}", display_name=f"TEST-US-UB-{suffix}",
                       status="online", model="LiteBeam 5AC", lifecycle_status="EOL", eol_date=date(2025, 1, 31))
        db.add_all([router, radio])
        db.flush()
        severe = SecurityAdvisory(cve_id=f"CVE-2098-{int(suffix, 16) % 100000:05d}", vendor="mikrotik", severity="critical", cvss=9.8)
        low = SecurityAdvisory(cve_id=f"CVE-2097-{int(suffix, 16) % 100000:05d}", vendor="mikrotik", severity="low", cvss=2.0)
        db.add_all([severe, low])
        db.flush()
        db.add_all([
            DeviceVulnerability(advisory_id=severe.id, device_id=router.id, status="open", installed_version="7.10", fixed_version="7.19.4"),
            DeviceVulnerability(advisory_id=severe.id, device_id=radio.id, status="resolved"),
            DeviceVulnerability(advisory_id=low.id, device_id=router.id, status="open"),
            ActionIssue(customer_id=customer.id, device_id=router.id, category="security", severity="high", status="open",
                        title=f"CI security issue {suffix}"),
            ActionIssue(customer_id=customer.id, category="backup", severity="high", status="open", title=f"CI backup issue {suffix}"),
            User(username=f"ci-us-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-us-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True, customer_scope=[]),
        ])
        db.commit()
        ids = {"customer": str(customer.id), "other": str(other.id), "router": str(router.id), "radio": str(radio.id), "severe": str(severe.id)}

    assert TestClient(app).get(f"/api/ui/v1/customers/{ids['customer']}/security").status_code == 401
    client = login(f"ci-us-{suffix}")
    data = client.get(f"/api/ui/v1/customers/{ids['customer']}/security").json()

    assert [row["advisory"]["id"] for row in data["vulnerabilities"]] == [ids["severe"]], "only open severe CVEs"
    row = data["vulnerabilities"][0]
    assert row["device"]["id"] == ids["router"] and row["device"]["brand"]["symbol"] == "brand-mikrotik"
    assert row["advisory"]["cvss"] == 9.8 and row["installed_version"] == "7.10" and row["fixed_version"] == "7.19.4"

    assert [d["id"] for d in data["firmware"]] == [ids["router"]]
    assert data["firmware"][0]["recommended_firmware_version"] == "7.19.4" and data["firmware"][0]["firmware_status"] == "outdated"
    assert [d["id"] for d in data["lifecycle"]] == [ids["radio"]]
    assert data["lifecycle"][0]["brand"]["symbol"] == "brand-ubiquiti" and data["lifecycle"][0]["eol_date"] == "2025-01-31"
    assert [i["title"] for i in data["issues"]] == [f"CI security issue {suffix}"], "only security / firmware / lifecycle issues"
    assert data["compliance"] is not None and {"evaluated_devices", "failing_devices", "counts", "failing_controls"} <= set(data["compliance"])

    assert client.get(f"/api/ui/v1/customers/{uuid.uuid4()}/security").status_code == 404
    with SessionLocal() as db:
        from sqlalchemy import select
        db.scalar(select(User).where(User.username == f"ci-us-scoped-{suffix}")).customer_scope = [ids["other"]]
        db.commit()
    scoped = login(f"ci-us-scoped-{suffix}")
    assert scoped.get(f"/api/ui/v1/customers/{ids['customer']}/security").status_code == 404, "customer scope"

    page = client.get(f"/customers/{ids['customer']}/security")
    assert page.status_code == 200 and f"TEST-US-MT-{suffix}" in page.text, "classic tab still renders from the shared data"
    assert client.get(f"/app/customers/{ids['customer']}/security").status_code in (200, 503)
    print("ui customer security smoke ok")


if __name__ == "__main__":
    main()
