import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "Strong-CI16-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "CSRF token missing"
    return match.group(1)


def seed():
    with SessionLocal() as db:
        old = db.scalar(select(Customer).where(Customer.code == "CI16"))
        if old:
            db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci16admin"))
        if old_user:
            db.delete(old_user)
        db.commit()
        user = User(username="ci16admin", password_hash=hash_password(PASSWORD), display_name="CI16 Admin", role="admin", is_active=True)
        customer = Customer(name="CI16 Device Workspace", code="CI16")
        db.add_all([user, customer])
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="CI16 MikroTik", display_name="CI16 Router", management_source="mikrotik_agent", status="pending_enrollment")
        db.add(device)
        db.flush()
        token, _ = core.create_enrollment(db, device, user)
        db.commit()
        return device.id, token


def login(client):
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post("/login", data={"username": "ci16admin", "password": PASSWORD, "csrf": csrf}, follow_redirects=False)
    assert response.status_code == 303


def main():
    device_id, token = seed()
    client = TestClient(app)

    enroll = client.post("/api/v1/agents/mikrotik/enroll", json={"token": token, "inventory": {"identity": "CI16-CCR", "model": "CCR2004-1G-12S+2XS", "routeros_version": "7.20.2 (stable)", "serial_number": "CI16SERIAL", "primary_mac": "02:16:00:00:00:01", "agent_version": "0.16.0"}})
    assert enroll.status_code == 200, enroll.text
    payload = enroll.json()
    assert payload["agent_version"] >= "0.16.0"
    source = payload["agent_source"]
    assert 'snapshot_section' in source
    assert 'diagnostic_ping' in source
    assert 'diagnostic_traceroute' in source
    assert 'backup_mikrotik' in source
    match = re.search(r':local nsmSecret "([^"]+)"', source)
    assert match
    secret = match.group(1)
    headers = {"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": secret}

    login(client)
    overview = client.get(f"/devices/{device_id}")
    assert overview.status_code == 200
    assert "Panoramica" in overview.text and "Configurazione" in overview.text and "Diagnostica" in overview.text
    assert "CI16SERIAL" in overview.text

    config = client.get(f"/devices/{device_id}/configuration?section=interfaces")
    assert config.status_code == 200
    csrf = csrf_from(config.text)
    queued = client.post(f"/devices/{device_id}/snapshot/interfaces", data={"csrf": csrf}, follow_redirects=False)
    assert queued.status_code == 303

    heartbeat = client.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"agent_version": payload["agent_version"], "inventory": {"identity": "CI16-CCR"}, "metrics": {"cpu_load": "17", "free_memory": "800MiB", "uptime": "2d01:00:00"}})
    assert heartbeat.status_code == 200, heartbeat.text
    jobs = heartbeat.json()["jobs"]
    snapshot = next(j for j in jobs if j["type"] == "snapshot_section")
    assert snapshot["payload"]["section"] == "interfaces"

    result = client.post(f"/api/v1/agents/mikrotik/jobs/{snapshot['id']}/complete", headers=headers, json={"status": "success", "result": {"section": "interfaces", "data": [{"name": "ether1", "type": "ether", "mac-address": "02:16:00:00:00:01", "mtu": "1500", "running": "true"}]}})
    assert result.status_code == 200, result.text
    config = client.get(f"/devices/{device_id}/configuration?section=interfaces")
    assert "ether1" in config.text and "02:16:00:00:00:01" in config.text

    diag = client.get(f"/devices/{device_id}/diagnostics")
    csrf = csrf_from(diag.text)
    queued = client.post(f"/devices/{device_id}/diagnostics/ping", data={"csrf": csrf, "target": "8.8.8.8"}, follow_redirects=False)
    assert queued.status_code == 303
    bad = client.post(f"/devices/{device_id}/diagnostics/ping", data={"csrf": csrf, "target": "8.8.8.8; /system reboot"}, follow_redirects=False)
    assert bad.status_code == 400

    heartbeat = client.post("/api/v1/agents/mikrotik/heartbeat", headers=headers, json={"agent_version": payload["agent_version"], "inventory": {}, "metrics": {}})
    diagnostics = [j for j in heartbeat.json()["jobs"] if j["type"] == "diagnostic_ping"]
    assert diagnostics
    diag_job = diagnostics[0]
    complete = client.post(f"/api/v1/agents/mikrotik/jobs/{diag_job['id']}/complete", headers=headers, json={"status": "success", "result": {"target": "8.8.8.8", "data": [{"time": "12ms", "status": "echo reply"}]}})
    assert complete.status_code == 200
    diag_page = client.get(f"/devices/{device_id}/diagnostics")
    assert "8.8.8.8" in diag_page.text and "echo reply" in diag_page.text

    with SessionLocal() as db:
        snapshot_job = db.get(DeviceJob, snapshot["id"])
        assert snapshot_job.status == "success"
        assert snapshot_job.result["data"][0]["name"] == "ether1"

    print("Core 0.16 MikroTik device workspace smoke test passed")


if __name__ == "__main__":
    main()
