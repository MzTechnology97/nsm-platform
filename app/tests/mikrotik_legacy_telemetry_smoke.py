import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_agent as agent
from app.agent_models import DeviceAgentCredential, DeviceMetricSample
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.security import hash_password

TEST_PASSWORD = "test-only-legacy-telemetry"
TEST_SECRET = "test-only-legacy-telemetry-secret"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def login(client: TestClient, username: str) -> None:
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post(
        "/login",
        data={"username": username, "password": TEST_PASSWORD, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303


def seed():
    suffix = uuid.uuid4().hex[:8]
    username = f"legacy-telemetry-{suffix}"
    with SessionLocal() as db:
        user = User(username=username, password_hash=hash_password(TEST_PASSWORD), display_name="Legacy Telemetry Test", role="admin", is_active=True)
        customer = Customer(name=f"Legacy Telemetry {suffix}", code=f"LT{suffix[:6]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="RouterOS 7.12.1 legacy telemetry",
            display_name="Legacy 7.12 Telemetry",
            management_source="mikrotik_agent",
            status="online",
            firmware_version="7.12.1",
            inventory_data={
                "agent_transport": "legacy",
                "agent_version": "0.49.0-legacy",
                "legacy_agent": True,
            },
        )
        db.add(device)
        db.flush()
        db.add(
            DeviceAgentCredential(
                device_id=device.id,
                agent_type="mikrotik_agent",
                secret_hash=agent._secret_digest(TEST_SECRET),
                is_active=True,
            )
        )
        db.commit()
        return username, device.id


def main():
    username, device_id = seed()
    client = TestClient(app, base_url="http://192.0.2.80")
    headers = {
        "X-NSM-Legacy-Transport": "headers-v1",
        "X-NSM-Device-ID": str(device_id),
        "X-NSM-Device-Secret": TEST_SECRET,
        "X-NSM-Agent-Version": "0.49.0-legacy",
        "X-NSM-Identity": "TEST-LEGACY-712",
        "X-NSM-Model": "wAP R",
        "X-NSM-RouterOS": "7.12.1",
        "X-NSM-Architecture": "mipsbe",
        "X-NSM-Uptime": "1d02:03:04",
        "X-NSM-CPU": "MIPS 1004Kc V2.15",
        "X-NSM-CPU-Count": "1",
        "X-NSM-CPU-Load": "17",
        "X-NSM-Total-Memory": "64MiB",
        "X-NSM-Free-Memory": "48MiB",
    }
    heartbeat = client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers=headers, content=b"")
    assert heartbeat.status_code == 200, heartbeat.text
    payload = heartbeat.json()
    assert payload["status"] == "ok"
    assert payload["telemetry_sampled"] is True

    with SessionLocal() as db:
        sample = db.scalar(
            select(DeviceMetricSample)
            .where(DeviceMetricSample.device_id == device_id)
            .order_by(DeviceMetricSample.observed_at.desc())
        )
        assert sample is not None
        assert sample.source == "mikrotik_agent_legacy"
        assert sample.cpu_load == 17
        assert sample.total_memory_bytes == 64 * 1024 * 1024
        assert sample.free_memory_bytes == 48 * 1024 * 1024
        assert sample.uptime_text == "1d02:03:04"

    login(client, username)
    metrics = client.get(f"/api/v1/devices/{device_id}/metrics?range=1h")
    assert metrics.status_code == 200, metrics.text
    assert metrics.json()["sample_count"] >= 1
    assert metrics.json()["points"][-1]["cpu_load"] == 17

    monitor = client.get(f"/devices/{device_id}/monitor")
    assert monitor.status_code == 200, monitor.text
    assert "Storico non supportato" not in monitor.text
    assert "data-telemetry-root" in monitor.text

    status = client.get(f"/devices/{device_id}/agent")
    assert status.status_code == 200, status.text
    assert "Retention 90 giorni · metriche heartbeat legacy" in status.text

    print("RouterOS 7.12 legacy telemetry history smoke passed")


if __name__ == "__main__":
    main()
