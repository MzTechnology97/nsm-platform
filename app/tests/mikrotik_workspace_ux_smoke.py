import re
import uuid

from fastapi.testclient import TestClient

from app.mikrotik_agent_generation import TARGET_AGENT_VERSION as CURRENT_AGENT
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

TEST_PASSWORD = "WorkspaceTestA1"


def csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        user = User(username=f"workspace-{suffix}", password_hash=hash_password(TEST_PASSWORD), display_name="Workspace UX Test", role="admin", is_active=True)
        customer = Customer(name=f"Workspace Customer {suffix}", code=f"W43{suffix[:5]}")
        db.add_all([user, customer])
        db.flush()
        legacy = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="Legacy Device", display_name="Legacy wAP R", device_identity="WAP-R-LEGACY", model="wAP R", serial_number="SERIAL43", primary_mac="AA:BB:CC:43:00:01", management_ip="192.0.2.43", firmware_version="7.12.1 (stable)", status="online", last_seen=now, management_source="mikrotik_agent", inventory_data={"agent_version":"0.20.0-legacy","agent_transport":"legacy","legacy_agent":True,"free_memory":"24522752","total_memory":"67108864","metrics":{"cpu_load":"8","free_memory":"24522752","total_memory":"67108864","uptime":"2d01:02:03"}})
        modern = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="Modern Device", display_name="Modern CCR", device_identity="CCR-MODERN", model="CCR2004-1G-12S+2XS", primary_mac="AA:BB:CC:43:00:02", firmware_version="7.20.7 (stable)", status="online", last_seen=now, management_source="mikrotik_agent", inventory_data={"agent_version":CURRENT_AGENT,"agent_transport":"modern"})
        db.add_all([legacy, modern])
        db.flush()
        diagnostic = DeviceJob(device_id=legacy.id, job_type="diagnostic_ping", status="success", payload={"target":"8.8.8.8","source":""}, result={"output":"HOST SIZE TTL TIME STATUS\n8.8.8.8 56 117 12ms ok"}, created_at=now, delivered_at=now, completed_at=now, attempts=1)
        db.add(diagnostic)
        db.commit()
        return user.username, legacy.id, modern.id, diagnostic.id


def login(client, username):
    page = client.get("/login")
    response = client.post("/login", data={"username":username,"password":TEST_PASSWORD,"csrf":csrf(page.text)}, follow_redirects=False)
    assert response.status_code == 303


def main():
    username, legacy_id, modern_id, diagnostic_id = seed()
    client = TestClient(app)
    login(client, username)
    overview = client.get(f"/api/ui/v1/devices/{legacy_id}")
    assert overview.status_code == 200 and "AA:BB:CC:43:00:01" in overview.text
    assert "monitor" in {tab["key"] for tab in overview.json()["tabs"]}, "legacy agents keep the monitor tab"
    configuration = client.get(f"/api/ui/v1/devices/{legacy_id}/configuration")
    assert configuration.status_code == 200 and configuration.json()["can_refresh"] is True
    diagnostics = client.get(f"/api/ui/v1/devices/{legacy_id}/diagnostics").json()
    assert diagnostics["transport"] == "legacy" and diagnostics["support_snapshot"] is False
    assert str(diagnostic_id) in {job["id"] for job in diagnostics["jobs"]}
    ui_headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    # A legacy agent older than 0.49.3 has no snapshot handler: the read is refused up front (MTK-05 capability gate).
    snapshot_refused = client.post(f"/api/ui/v1/devices/{legacy_id}/configuration/refresh", headers=ui_headers, json={"section": "resources"})
    assert snapshot_refused.status_code == 422 and snapshot_refused.json()["detail"]["code"] == "section_unsupported", snapshot_refused.text
    assert "0.49.3" in snapshot_refused.json()["detail"]["message"]
    support_blocked = client.post(f"/api/ui/v1/devices/{legacy_id}/diagnostics/support_snapshot", headers=ui_headers)
    assert support_blocked.status_code == 409 and "Support snapshot non disponibile" in support_blocked.json()["detail"]["message"]
    detail = client.get(f"/api/ui/v1/devices/{legacy_id}/diagnostics/jobs/{diagnostic_id}/output")
    assert detail.status_code == 200 and "8.8.8.8" in detail.text and "12ms" in detail.text
    modern_refresh = client.post(f"/api/ui/v1/devices/{modern_id}/configuration/refresh", headers=ui_headers, json={"section": "all"})
    assert modern_refresh.status_code == 200 and modern_refresh.json()["queued"] is True, modern_refresh.text
    print("Core 0.43 real-device workspace UX smoke passed")


if __name__ == "__main__":
    main()
