"""New interface, dashboard API: summary counts, distributions (readable vendors), outages by age, resource pressure, CVE posture,
latest advisories with affected devices, recent events, sign-in required."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, DeviceVulnerability, SecurityAdvisory, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Dashboard-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    anonymous = TestClient(app)
    assert anonymous.get("/api/ui/v1/dashboard").status_code == 401

    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    vendor = f"civendor{suffix}"
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Dashboard {suffix}", code=f"UD{suffix}")
        db.add(customer)
        db.flush()
        devices = [
            Device(customer_id=customer.id, vendor=vendor, device_type="router", name=f"TEST-UD-{suffix}-{index}", display_name=f"TEST-UD-{suffix}-{index}",
                   status=status, model=f"CI-MODEL-{suffix}", firmware_version=f"9.{index}.{suffix}")
            for index, status in enumerate(("online", "online", "offline"))
        ]
        # Two more: one under heavy load (UISP metrics), one down for two days; the third above went down just now.
        devices[2].last_seen = now + timedelta(days=3650)
        loaded = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UD-{suffix}-hot", status="online",
                        inventory_data={"uisp": {"metrics": {"cpu_percent": 100, "ram_percent": 40}}})
        stale = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-UD-{suffix}-stale", status="offline",
                       last_seen=now - timedelta(days=2))
        db.add_all([*devices, loaded, stale])
        db.flush()
        advisory = SecurityAdvisory(cve_id=f"CVE-2099-{int(suffix, 16) % 100000:05d}", vendor=vendor, product="CI product", severity="critical",
                                    published_at=now + timedelta(days=3650))
        db.add(advisory)
        db.flush()
        db.add_all([
            DeviceVulnerability(advisory_id=advisory.id, device_id=devices[0].id, status="open"),
            DeviceVulnerability(advisory_id=advisory.id, device_id=devices[1].id, status="resolved"),
        ])
        kind = f"CI_UI_DASHBOARD_{suffix.upper()}"
        db.add(AuditEvent(event_type=kind, customer_id=customer.id, timestamp=now + timedelta(days=3650), severity="info", result="success", source="ci"))
        db.add(User(username=f"ci-ud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
        db.commit()
        advisory_id = str(advisory.id)

    client = login(f"ci-ud-{suffix}")
    response = client.get("/api/ui/v1/dashboard")
    assert response.status_code == 200, response.text
    data = response.json()
    for key in ("customer_count", "site_count", "device_count", "online_count", "offline_count", "pending_count", "eol_count",
                "firmware_attention_count", "open_issue_count", "critical_issue_count", "severe_cve_device_count", "backup_failed_count",
                "backup_protected_count", "backup_blocked_count", "backup_no_policy_count", "backup_coverage_percent", "updated_at"):
        assert key in data, key
    assert data["device_count"] >= 3 and data["online_count"] >= 2 and data["offline_count"] >= 1 and data["customer_count"] >= 1

    for distribution in ("vendor_distribution", "firmware_distribution", "model_distribution"):
        rows = data[distribution]
        assert len(rows) <= 6 and all({"key", "label", "count", "percent"} <= set(row) for row in rows), distribution
    assert all(row["label"] == "MikroTik" for row in data["vendor_distribution"] if row["key"] == "mikrotik"), "readable vendor names"
    assert any(row["key"] == vendor and row["label"] == vendor for row in data["vendor_distribution"]) or len(data["vendor_distribution"]) == 6

    aging = data["offline_aging"]
    assert set(aging) == {"hour", "day", "week", "older", "never"} and aging["hour"] >= 1 and aging["week"] >= 1
    assert sum(aging.values()) == data["offline_count"]
    outage = data["recent_outages"][0]
    assert outage["name"] == f"TEST-UD-{suffix}-2" and outage["customer"]["name"] == f"CI UI Dashboard {suffix}" and outage["brand"], outage
    pressure = data["resource_pressure"]
    assert pressure["hot"] >= 1 and pressure["reporting"] >= 1
    assert any(row["name"] == f"TEST-UD-{suffix}-hot" and row["telemetry"]["cpu"] == 100 for row in pressure["top"]), pressure["top"]
    posture = data["cve_posture"]
    assert posture["severe"] >= 1 and sum(posture.values()) == data["device_count"]

    advisory = data["latest_advisories"][0]
    assert advisory["id"] == advisory_id and advisory["severity"] == "critical" and advisory["vendor"] == vendor
    assert advisory["affected"] == 1, "resolved findings are not counted"

    event = data["recent_events"][0]
    assert event["event_type"] == kind and event["label"] and event["result"] == "success"

    assert client.get("/app/").status_code in (200, 503)
    print("ui dashboard smoke ok")


if __name__ == "__main__":
    main()
