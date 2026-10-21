"""New interface, sidebar counters (/api/ui/v1/nav-counts): one figure per permitted page, customer scope, decommissioned devices left out."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, Customer, Device, DeviceVulnerability, SecurityAdvisory, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Nav-Counts-2026"
KEYS = {"devices", "offline", "severe_cve_devices", "firmware_attention", "open_issues", "critical_issues"}


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        mine = Customer(name=f"CI Nav Counts {suffix}", code=f"NC{suffix}")
        other = Customer(name=f"CI Nav Counts other {suffix}", code=f"ND{suffix}")
        db.add_all([mine, other])
        db.flush()
        down = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-NC-DOWN-{suffix}", status="offline",
                      management_ip="198.51.100.71", firmware_status="outdated")
        up = Device(customer_id=mine.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-NC-UP-{suffix}", status="online",
                    management_ip="198.51.100.72")
        retired = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-NC-OLD-{suffix}", status="offline",
                         management_ip="198.51.100.73", firmware_status="outdated", decommissioned_at=utcnow())
        foreign = Device(customer_id=other.id, vendor="mikrotik", device_type="router", name=f"TEST-NC-OT-{suffix}", status="offline",
                         management_ip="203.0.113.74", firmware_status="outdated")
        db.add_all([down, up, retired, foreign])
        db.flush()
        severe = SecurityAdvisory(cve_id=f"CVE-2099-{int(suffix, 16) % 90000 + 10000}", severity="critical", summary="CI nav counts")
        db.add(severe)
        db.flush()
        for device in (down, retired, foreign):
            db.add(DeviceVulnerability(advisory_id=severe.id, device_id=device.id, status="open"))
        for title, severity, status, customer, device in (
            ("down", "critical", "open", mine, down),
            ("slow", "warning", "acknowledged", mine, up),
            ("closed", "critical", "resolved", mine, up),
            ("foreign", "critical", "open", other, foreign),
        ):
            db.add(ActionIssue(title=f"TEST {title} {suffix}", severity=severity, category="reachability", status=status, details={},
                               customer_id=customer.id, device_id=device.id))
        db.add_all([
            User(username=f"ci-nc-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-nc-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True,
                 customer_scope=[str(mine.id)]),
            User(username=f"ci-nc-none-{suffix}", password_hash=hash_password(PASSWORD), role="ci-no-permissions", is_active=True),
        ])
        db.commit()

    assert TestClient(app).get("/api/ui/v1/nav-counts").status_code == 401, "signed out"

    admin = login(f"ci-nc-{suffix}").get("/api/ui/v1/nav-counts")
    assert admin.status_code == 200 and set(admin.json()) == KEYS, admin.json()
    everything = admin.json()
    assert everything["devices"] >= 3 and everything["offline"] >= 2 and everything["severe_cve_devices"] >= 2, everything
    assert everything["open_issues"] >= 3 and everything["critical_issues"] >= 2, everything

    # Limited to one customer: only its active devices and issues; the decommissioned device is not counted.
    scoped = login(f"ci-nc-scoped-{suffix}").get("/api/ui/v1/nav-counts").json()
    assert scoped == {"devices": 2, "offline": 1, "severe_cve_devices": 1, "firmware_attention": 1, "open_issues": 2, "critical_issues": 1}, scoped

    # No permission on any of the pages: no figure at all.
    none = login(f"ci-nc-none-{suffix}").get("/api/ui/v1/nav-counts")
    assert none.status_code == 200 and none.json() == {}, none.json()
    print("ui nav counts smoke ok")


if __name__ == "__main__":
    main()
