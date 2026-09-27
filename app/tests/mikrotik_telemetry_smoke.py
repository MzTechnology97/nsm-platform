import os
import re

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import main as core
from app.agent_models import DeviceMetricSample
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_telemetry import memory_bytes
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "Strong-CI19-Password-2026"
STAGE = os.getenv("NSM_TELEMETRY_STAGE", "all")


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    with SessionLocal() as db:
        old = db.scalar(select(Customer).where(Customer.code == "CI19"))
        if old:
            db.delete(old)
        old_user = db.scalar(select(User).where(User.username == "ci19admin"))
        if old_user:
            db.delete(old_user)
        db.commit()
        user = User(username="ci19admin", password_hash=hash_password(PASSWORD), display_name="CI19 Admin", role="admin", is_active=True)
        customer = Customer(name="CI19 Telemetry Lab", code="CI19")
        db.add_all([user, customer])
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="CI19 MikroTik", display_name="CI19 Router", management_source="mikrotik_agent", status="pending_enrollment")
        db.add(device)
        db.flush()
        token, _ = core.create_enrollment(db, device, user)
        db.commit()
        return device.id, token


def login(client):
    page = client.get("/login")
    csrf = csrf_from(page.text)
    response = client.post("/login", data={"username":"ci19admin","password":PASSWORD,"csrf":csrf}, follow_redirects=False)
    assert response.status_code == 303


def run_memory_stage():
    assert memory_bytes("790MiB") == 790 * 1024 * 1024
    assert memory_bytes("1.5GiB") == int(1.5 * 1024**3)
    assert memory_bytes(1024) == 1024
    assert memory_bytes("invalid") is None


def enrolled_client():
    device_id, token = seed()
    client = TestClient(app)
    enroll = client.post("/api/v1/agents/mikrotik/enroll", json={"token":token,"inventory":{"identity":"CI19-CCR","model":"CCR2004","routeros_version":"7.20.2","serial_number":"CI19SERIAL","total_memory":"1024MiB","free_memory":"800MiB","uptime":"1d00:00:00","agent_version":"0.16.0"}})
    assert enroll.status_code == 200, enroll.text
    match = re.search(r':local nsmSecret "([^"]+)"', enroll.json()["agent_source"])
    assert match
    headers={"X-NSM-Device-ID":str(device_id),"X-NSM-Device-Secret":match.group(1)}
    return device_id, client, headers


def heartbeat(client, headers, cpu="12", free="800MiB", uptime="1d00:05:00"):
    return client.post("/api/v1/agents/mikrotik/heartbeat",headers=headers,json={"agent_version":"0.16.0","inventory":{"identity":"CI19-CCR","total_memory":"1024MiB","uptime":uptime},"metrics":{"cpu_load":str(cpu),"free_memory":free,"uptime":uptime}})


def run_heartbeat_stage():
    device_id, client, headers = enrolled_client()
    if STAGE == "enroll":
        return device_id, client

    first = heartbeat(client, headers)
    assert first.status_code == 200, first.text
    if STAGE == "heartbeat-http":
        return device_id, client
    assert first.json().get("telemetry_sampled") is True, first.text
    if STAGE == "heartbeat-sampled":
        return device_id, client

    second = heartbeat(client, headers, 34, "700MiB", "1d00:10:00")
    third = heartbeat(client, headers, 18, "760MiB", "1d00:15:00")
    for response in (second, third):
        assert response.status_code == 200, response.text
        assert response.json().get("telemetry_sampled") is True, response.text
    if STAGE == "heartbeat-response":
        return device_id, client

    with SessionLocal() as db:
        count=int(db.scalar(select(func.count(DeviceMetricSample.id)).where(DeviceMetricSample.device_id==device_id)) or 0)
        assert count == 3, f"expected 3 telemetry rows, got {count}"
        if STAGE == "heartbeat-count":
            return device_id, client
        rows=list(db.scalars(select(DeviceMetricSample).where(DeviceMetricSample.device_id==device_id).order_by(DeviceMetricSample.observed_at.asc(), DeviceMetricSample.id.asc())))
        assert [row.cpu_load for row in rows] == [12.0, 34.0, 18.0], [row.cpu_load for row in rows]
        last=rows[-1]
        assert last.free_memory_bytes == 760 * 1024 * 1024
        assert last.total_memory_bytes == 1024 * 1024 * 1024
    return device_id, client


def run_metrics_stage(device_id, client):
    login(client)
    metrics=client.get(f"/api/v1/devices/{device_id}/metrics?range=24h")
    assert metrics.status_code == 200, metrics.text
    body=metrics.json()
    assert body["sample_count"] == 3, body
    assert [point["cpu_load"] for point in body["points"]] == [12.0,34.0,18.0], body
    assert body["points"][-1]["memory_used_percent"] > 25
    invalid=client.get(f"/api/v1/devices/{device_id}/metrics?range=year")
    assert invalid.status_code == 400


def run_monitor_stage(device_id, client):
    monitor=client.get(f"/devices/{device_id}/monitor")
    assert monitor.status_code == 200, monitor.text
    for marker in ("Telemetria risorse","Retention 90 giorni","1h","24h","7g","30g","telemetry_monitor.js","telemetry_monitor.css"):
        assert marker in monitor.text, marker
    assert f"/api/v1/devices/{device_id}/metrics" in monitor.text


def main():
    run_memory_stage()
    if STAGE == "memory":
        print("Core 0.19 telemetry memory stage passed")
        return
    device_id, client = run_heartbeat_stage()
    if STAGE in {"enroll","heartbeat-http","heartbeat-sampled","heartbeat-response","heartbeat-count","heartbeat"}:
        print(f"Core 0.19 telemetry {STAGE} stage passed")
        return
    run_metrics_stage(device_id, client)
    if STAGE == "metrics":
        print("Core 0.19 telemetry metrics stage passed")
        return
    run_monitor_stage(device_id, client)
    print("Core 0.19 MikroTik telemetry smoke test passed")


if __name__ == "__main__":
    main()
