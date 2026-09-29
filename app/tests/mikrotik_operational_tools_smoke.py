import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main as core
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "Strong-CI20-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    with SessionLocal() as db:
        old = db.scalar(select(Customer).where(Customer.code == "CI20"))
        if old:
            db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci20admin"))
        if old_user:
            db.delete(old_user)
        db.commit()
        user = User(username="ci20admin", password_hash=hash_password(PASSWORD), display_name="CI20 Admin", role="admin", is_active=True)
        customer = Customer(name="CI20 Operational Tools", code="CI20")
        db.add_all([user, customer])
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="CI20 MikroTik", display_name="CI20 Router", management_source="mikrotik_agent", status="pending_enrollment")
        db.add(device)
        db.flush()
        token, _ = core.create_enrollment(db, device, user)
        db.commit()
        return device.id, token


def login(client):
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post("/login", data={"username": "ci20admin", "password": PASSWORD, "csrf": csrf}, follow_redirects=False)
    assert response.status_code == 303


def main():
    device_id, token = seed()
    client = TestClient(app)

    enroll = client.post("/api/v1/agents/mikrotik/enroll", json={"token": token, "inventory": {"identity": "CI20-CCR", "model": "CCR2004", "routeros_version": "7.20.2", "serial_number": "CI20SERIAL", "primary_mac": "02:20:00:00:00:01"}})
    assert enroll.status_code == 200, enroll.text
    body = enroll.json()
    agent_version = str(body.get("agent_version") or "").strip()
    assert agent_version, body
    source = body["agent_source"]
    for marker in ("diagnostic_neighbors", "diagnostic_dhcp_lookup", "diagnostic_logs", "support_snapshot", "src-address"):
        assert marker in source, marker
    assert ':execute script=' not in source
    assert 'command_b64' not in source

    match = re.search(r':local nsmSecret "([^"]+)"', source)
    assert match
    headers = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": match.group(1)}

    login(client)
    page = client.get(f"/devices/{device_id}/diagnostics")
    assert page.status_code == 200
    for marker in ("Neighbor discovery", "DHCP lookup", "Log dispositivo", "Support snapshot", "IP sorgente opzionale"):
        assert marker in page.text, marker
    csrf = csrf_from(page.text)

    requests = [
        ("ping", {"csrf": csrf, "target": "8.8.8.8", "source": "192.0.2.1"}),
        ("traceroute", {"csrf": csrf, "target": "1.1.1.1", "source": "192.0.2.1"}),
        ("neighbors", {"csrf": csrf}),
        ("dhcp_lookup", {"csrf": csrf, "query": "AA:BB:CC:DD:EE:FF"}),
        ("logs", {"csrf": csrf}),
        ("support_snapshot", {"csrf": csrf}),
    ]
    for diagnostic, data in requests:
        response = client.post(f"/devices/{device_id}/diagnostics/{diagnostic}", data=data, follow_redirects=False)
        assert response.status_code == 303, (diagnostic, response.text)

    bad_source = client.post(f"/devices/{device_id}/diagnostics/ping", data={"csrf": csrf, "target": "8.8.8.8", "source": "192.0.2.1; /system reboot"}, follow_redirects=False)
    assert bad_source.status_code == 400
    bad_dhcp = client.post(f"/devices/{device_id}/diagnostics/dhcp_lookup", data={"csrf": csrf, "query": "AA:BB:CC:DD:EE:FF; /system reboot"}, follow_redirects=False)
    assert bad_dhcp.status_code == 400

    heartbeat = client.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"agent_version": agent_version, "inventory": {"identity": "CI20-CCR"}, "metrics": {"cpu_load": "9", "free_memory": "900MiB", "uptime": "3d00:00:00"}})
    assert heartbeat.status_code == 200, heartbeat.text
    jobs = heartbeat.json()["jobs"]
    types = {job["type"] for job in jobs}
    expected = {"diagnostic_ping", "diagnostic_traceroute", "diagnostic_neighbors", "diagnostic_dhcp_lookup", "diagnostic_logs"}
    assert expected.issubset(types), (expected, types)

    by_type = {job["type"]: job for job in jobs}
    sample_results = {
        "diagnostic_ping": {"target": "8.8.8.8", "source": "192.0.2.1", "data": [{"time": "11ms", "status": "echo reply"}]},
        "diagnostic_traceroute": {"target": "1.1.1.1", "source": "192.0.2.1", "data": [{"address": "192.0.2.254", "hop": 1}]},
        "diagnostic_neighbors": {"data": [{"identity": "POP-SW", "interface": "ether2", "mac-address": "02:20:00:00:00:AA"}]},
        "diagnostic_dhcp_lookup": {"query": "AA:BB:CC:DD:EE:FF", "lookup_type": "mac", "data": [{"address": "192.0.2.55", "host-name": "client-test"}]},
        "diagnostic_logs": {"data": [{"time": "11:20:00", "topics": "system,error", "message": "test error"}]},
    }
    for job_type, result in sample_results.items():
        job = by_type[job_type]
        complete = client.post(f"/api/v1/agents/mikrotik/jobs/{job['id']}/complete", headers=headers, json={"status": "success", "result": result})
        assert complete.status_code == 200, complete.text

    heartbeat2 = client.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"agent_version": agent_version, "inventory": {}, "metrics": {}})
    support = next(job for job in heartbeat2.json()["jobs"] if job["type"] == "support_snapshot")
    complete = client.post(f"/api/v1/agents/mikrotik/jobs/{support['id']}/complete", headers=headers, json={"status": "success", "result": {"data": {"resources": {"identity": "CI20-CCR"}, "interfaces": [{"name": "ether1"}], "logs": []}}})
    assert complete.status_code == 200

    # Core 0.43 keeps the diagnostics overview compact: completed runs are listed
    # there, while full payload/output lives on the single-job detail page.
    page = client.get(f"/devices/{device_id}/diagnostics")
    assert page.status_code == 200
    for job_type, label in (("diagnostic_neighbors", "NEIGHBORS"), ("diagnostic_dhcp_lookup", "DHCP LOOKUP"), ("diagnostic_logs", "LOGS")):
        job_id = by_type[job_type]["id"]
        assert f"/devices/{device_id}/diagnostics/jobs/{job_id}" in page.text
        assert label in page.text

    detail_expectations = {
        "diagnostic_neighbors": "POP-SW",
        "diagnostic_dhcp_lookup": "client-test",
        "diagnostic_logs": "test error",
    }
    for job_type, marker in detail_expectations.items():
        job_id = by_type[job_type]["id"]
        detail = client.get(f"/devices/{device_id}/diagnostics/jobs/{job_id}")
        assert detail.status_code == 200
        assert marker in detail.text, (job_type, marker)

    assert f"/devices/{device_id}/diagnostics/jobs/{support['id']}" in page.text
    support_detail = client.get(f"/devices/{device_id}/diagnostics/jobs/{support['id']}")
    assert support_detail.status_code == 200
    assert "CI20-CCR" in support_detail.text

    print("Operational tools remain covered against the currently enrolled modern agent generation")


if __name__ == "__main__":
    main()
