import re
import uuid

from fastapi.testclient import TestClient

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
        modern = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="Modern Device", display_name="Modern CCR", device_identity="CCR-MODERN", model="CCR2004-1G-12S+2XS", primary_mac="AA:BB:CC:43:00:02", firmware_version="7.20.7 (stable)", status="online", last_seen=now, management_source="mikrotik_agent", inventory_data={"agent_version":"0.20.0","agent_transport":"modern"})
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
    overview = client.get(f"/devices/{legacy_id}")
    assert overview.status_code == 200 and "23.4 MiB" in overview.text and "64.0 MiB" in overview.text
    monitor = client.get(f"/devices/{legacy_id}/monitor")
    assert monitor.status_code == 200 and "AA:BB:CC:43:00:01" in monitor.text
    assert "8%" in monitor.text and "Storico non supportato" in monitor.text and "data-telemetry-root" not in monitor.text
    configuration = client.get(f"/devices/{legacy_id}/configuration")
    assert configuration.status_code == 200 and "Snapshot configurazione non disponibile" in configuration.text and "Aggiorna snapshot" not in configuration.text
    diagnostics = client.get(f"/devices/{legacy_id}/diagnostics")
    assert diagnostics.status_code == 200 and "NON DISP." in diagnostics.text
    assert f"/devices/{legacy_id}/diagnostics/jobs/{diagnostic_id}" in diagnostics.text
    token = csrf(diagnostics.text)
    assert client.post(f"/devices/{legacy_id}/snapshot/resources", data={"csrf":token}).status_code == 409
    assert client.post(f"/devices/{legacy_id}/diagnostics/support_snapshot", data={"csrf":token}).status_code == 409
    detail = client.get(f"/devices/{legacy_id}/diagnostics/jobs/{diagnostic_id}")
    assert detail.status_code == 200 and "8.8.8.8" in detail.text and "12ms" in detail.text
    modern_configuration = client.get(f"/devices/{modern_id}/configuration")
    assert modern_configuration.status_code == 200 and "Aggiorna snapshot" in modern_configuration.text
    print("Core 0.43 real-device workspace UX smoke passed")


if __name__ == "__main__":
    main()
