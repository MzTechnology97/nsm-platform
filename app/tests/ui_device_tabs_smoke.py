"""New interface, device Activity, Exposure and Compliance tabs: jobs without payloads, exposure data and actions, compliance order, scope."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.agent_models import DeviceJob
from app.compliance_models import ComplianceResult
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Device-Tabs-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Device Tabs {suffix}", code=f"DT{suffix}")
        other = Customer(name=f"CI UI Device Tabs other {suffix}", code=f"DU{suffix}")
        db.add_all([customer, other])
        db.flush()
        exposure = {"source": "agent", "exposed": 1, "worst": "high", "uncertain": 0, "wan_interfaces": ["ether1"], "firewall_rules": 4,
                    "raw_collected": True, "raw_rules": 1, "input_drop": False, "checked_at": now.isoformat(),
                    "findings": [{"label": "Winbox", "why": "synthetic", "proto": "tcp", "port": 8291, "state": "exposed", "severity": "high", "reason": "no drop"}],
                    "port_forwards": [{"rule": 3, "protocol": "tcp", "public_ports": "8443", "to_address": "192.0.2.50", "to_ports": "443",
                                       "sensitive": ["https"], "certain": True, "severity": "high"}]}
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DT-MT-{suffix}", display_name=f"TEST-DT-MT-{suffix}",
                        status="online", inventory_data={"agent_transport": "modern", "agent_version": "0.49.40", "exposure": exposure})
        cpe = Device(customer_id=customer.id, vendor="generic", device_type="router", name=f"TEST-DT-GEN-{suffix}", display_name=f"TEST-DT-GEN-{suffix}",
                     status="online", management_ip="198.51.100.20")
        hidden = Device(customer_id=other.id, vendor="generic", device_type="router", name=f"TEST-DT-HID-{suffix}", status="online")
        db.add_all([router, cpe, hidden])
        db.flush()
        for index in range(55):
            db.add(DeviceJob(device_id=router.id, job_type="snapshot_section" if index % 2 else "backup_export", status="success" if index else "failed",
                             payload={"upload_token": f"secret-{suffix}"}, result={"note": "x"}, created_at=now - timedelta(minutes=index),
                             last_error="boom" if index == 0 else None))
        db.add(DeviceJob(device_id=router.id, job_type="reboot", status="pending", payload={}, created_at=now + timedelta(seconds=5)))
        db.add_all([
            ComplianceResult(device_id=router.id, control_id="backup_recent", status="pass", evidence="ok", evaluated_at=now, status_changed_at=now),
            ComplianceResult(device_id=router.id, control_id="firmware_current", status="fail", evidence="old", evaluated_at=now, status_changed_at=now),
            ComplianceResult(device_id=router.id, control_id="no_unhandled_severe_vulnerabilities", status="fail", evidence="cve", evaluated_at=now,
                             status_changed_at=now, exception_until=now + timedelta(days=3), exception_reason="ci"),
        ])
        db.add_all([
            User(username=f"ci-dt-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-dt-aud-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True, customer_scope=[str(customer.id)]),
        ])
        db.commit()
        ids = {"router": str(router.id), "cpe": str(cpe.id), "hidden": str(hidden.id)}

    admin, admin_csrf = login(f"ci-dt-{suffix}")
    auditor, auditor_csrf = login(f"ci-dt-aud-{suffix}")
    base = f"/api/ui/v1/devices/{ids['router']}"
    assert TestClient(app).get(f"{base}/jobs").status_code == 401

    # Jobs: newest first, paged by 50, filters, active count, never payloads or results.
    jobs = auditor.get(f"{base}/jobs").json()
    assert jobs["total"] == 56 and len(jobs["items"]) == 50 and jobs["active"] == 1
    assert jobs["items"][0]["job_type"] == "reboot" and {"backup_export", "reboot", "snapshot_section"} == set(jobs["job_types"])
    assert f"secret-{suffix}" not in str(jobs) and "payload" not in jobs["items"][0] and "result" not in jobs["items"][0]
    assert jobs["items"][1]["last_error"] == "boom" and len(auditor.get(f"{base}/jobs", params={"offset": 50}).json()["items"]) == 6
    assert auditor.get(f"{base}/jobs", params={"status": "failed"}).json()["total"] == 1
    assert auditor.get(f"{base}/jobs", params={"job_type": "reboot"}).json()["total"] == 1
    assert auditor.get(f"{base}/jobs", params={"q": "boom"}).json()["total"] == 1
    assert auditor.get(f"/api/ui/v1/devices/{ids['hidden']}/jobs").status_code == 404, "customer scope"

    # Exposure (agent mode): last result, forwards, permissions on actions.
    tab = auditor.get(f"{base}/exposure").json()
    assert tab["check_mode"] == "agent" and tab["blocker"] is None and tab["can_remediate"] is False
    assert tab["exposure"]["findings"][0]["port"] == 8291 and tab["exposure"]["port_forwards"][0]["to_address"] == "192.0.2.50"
    assert auditor.post(f"{base}/exposure/check", headers=auditor_csrf).status_code == 403
    assert admin.post(f"{base}/exposure/check").status_code == 403, "CSRF header required"
    started = admin.post(f"{base}/exposure/check", headers=admin_csrf).json()
    assert started["started"] is True and started["message"]
    assert admin.get(f"{base}/exposure").json()["pending"] is True

    # Exposure (external mode): target IP validation and save.
    cpe = f"/api/ui/v1/devices/{ids['cpe']}/exposure"
    external = admin.get(cpe).json()
    assert external["check_mode"] == "external" and external["planned"]
    assert external["target"]["ip"] is None and external["blocker"], "a documentation management IP is not public: the operator must set one"
    bad = admin.post(f"{cpe}/target", headers=admin_csrf, json={"target_ip": "127.0.0.1"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "ip_invalid"
    assert "queued" in admin.post(f"{cpe}/target", headers=admin_csrf, json={"target_ip": "203.0.113.9"}).json()
    after = admin.get(cpe).json()["target"]
    assert after["ip"] == "203.0.113.9" and after["source"] == "operator" and after["override"] == "203.0.113.9" and after["public"] is False
    admin.post(f"{cpe}/target", headers=admin_csrf, json={"target_ip": ""})
    assert admin.get(cpe).json()["target"]["ip"] is None

    # Compliance: failures first, exception flagged.
    rows = auditor.get(f"{base}/compliance").json()
    states = [(row["control_id"], row["status"], row["excepted"]) for row in rows["rows"]]
    assert states[0][1] == "fail" and states[-1] == ("backup_recent", "pass", False)
    assert ("no_unhandled_severe_vulnerabilities", "fail", True) in states and rows["last_eval"]

    for tab_name in ("jobs", "exposure", "compliance"):
        assert admin.get(f"/app/devices/{ids['router']}/{tab_name}").status_code in (200, 503)
    print("ui device tabs smoke ok")


if __name__ == "__main__":
    main()
