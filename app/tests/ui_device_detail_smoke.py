"""New interface, device overview API: identity, agent, issues, CVEs, backup, ping, tabs per vendor, actions per role, scope."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, BackupRun, Customer, Device, DeviceVulnerability, SecurityAdvisory, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Device-Detail-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        mine = Customer(name=f"CI Detail {suffix}", code=f"DD{suffix[:6]}")
        other = Customer(name=f"CI Detail other {suffix}", code=f"DO{suffix[:6]}")
        db.add_all([mine, other])
        db.flush()
        router = Device(customer_id=mine.id, vendor="mikrotik", device_type="router", name=f"TEST-DD-RTR-{suffix}", display_name="Router sede",
                        device_identity="EDGE-DD", status="online", last_seen=now, management_ip="198.51.100.30", firmware_version="7.16.2",
                        recommended_firmware_version="7.19.4", firmware_status="outdated", serial_number="SN-DD-1", primary_mac="02:DD:00:00:00:01",
                        inventory_data={"agent_transport": "modern", "agent_version": "0.49.22", "last_heartbeat_at": now.isoformat(), "lan_ip": "192.0.2.1",
                                        "icmp_monitor": {"enabled": True, "last_rtt": 12.5, "last_loss": 0, "consecutive_losses": 0}})
        cpe = Device(customer_id=mine.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-DD-CPE-{suffix}", status="offline")
        foreign = Device(customer_id=other.id, vendor="mikrotik", device_type="router", name=f"TEST-DD-FOREIGN-{suffix}", status="online")
        db.add_all([router, cpe, foreign])
        db.flush()
        advisory = SecurityAdvisory(cve_id=f"CVE-2099-{int(suffix, 16) % 90000 + 10000}", source="manual", severity="high")
        db.add(advisory)
        db.flush()
        db.add(DeviceVulnerability(device_id=router.id, advisory_id=advisory.id, status="open"))
        db.add(ActionIssue(category="monitoring", severity="warning", status="open", title="TEST agent fermo", customer_id=mine.id, device_id=router.id))
        db.add(BackupRun(device_id=router.id, status="success", started_at=now - timedelta(hours=3), completed_at=now - timedelta(hours=3)))
        db.add_all([
            User(username=f"ci-dd-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-dd-op-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True),
            User(username=f"ci-dd-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {"router": str(router.id), "cpe": str(cpe.id), "foreign": str(foreign.id)}

    assert TestClient(app).get(f"/api/ui/v1/devices/{ids['router']}").status_code == 401
    admin = login(f"ci-dd-{suffix}")
    body = admin.get(f"/api/ui/v1/devices/{ids['router']}").json()
    device = body["device"]
    assert device["name"] == "Router sede" and device["identity"] == "EDGE-DD" and device["lan_ip"] == "192.0.2.1"
    assert device["recommended_firmware"] == "7.19.4" and device["customer"]["name"] == f"CI Detail {suffix}" and device["severe_cves"] == 1
    assert body["agent"]["version"] == "0.49.22" and body["agent"]["transport"] == "modern"
    assert body["vulnerabilities"] == {"high": 1}
    assert [i["title"] for i in body["issues"]] == ["TEST agent fermo"]
    assert body["backup"]["last_success"] is not None and body["backup"]["method"]
    assert body["icmp"]["enabled"] and body["icmp"]["last_rtt"] == 12.5 and body["icmp"]["target"] == "198.51.100.30"
    tabs = [t["key"] for t in body["tabs"]]
    assert tabs[0] == "overview" and {"monitor", "agent", "diagnostics", "configuration"} <= set(tabs) and "uisp" not in tabs
    assert next(t for t in body["tabs"] if t["key"] == "monitor")["classic"] == f"/devices/{ids['router']}/monitor"
    assert body["zabbix"] is None, "no Zabbix block when Zabbix is not configured"
    assert body["actions"] == {"reboot": True, "incident": True, "manage": True}

    cpe = admin.get(f"/api/ui/v1/devices/{ids['cpe']}").json()
    assert "uisp" in [t["key"] for t in cpe["tabs"]] and "agent" not in [t["key"] for t in cpe["tabs"]] and cpe["agent"] is None
    assert cpe["actions"]["reboot"] is False, "reboot is MikroTik only"
    assert admin.get(f"/api/ui/v1/devices/{uuid.uuid4()}").status_code == 404

    operator = login(f"ci-dd-op-{suffix}").get(f"/api/ui/v1/devices/{ids['router']}").json()
    assert operator["actions"] == {"reboot": False, "incident": True, "manage": False}, operator["actions"]  # operators open incidents, not reboot/manage

    scoped = login(f"ci-dd-scoped-{suffix}")
    assert scoped.get(f"/api/ui/v1/devices/{ids['router']}").status_code == 404, "another customer's device is invisible"
    assert scoped.get(f"/api/ui/v1/devices/{ids['foreign']}").status_code == 200
    assert admin.get(f"/app/devices/{ids['router']}").status_code in (200, 503), "deep link served by the shell"
    print("UI device detail smoke passed")


if __name__ == "__main__":
    main()
