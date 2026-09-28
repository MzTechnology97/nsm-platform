import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User
from app.security import hash_password

PASSWORD = "Strong-CI39-Password-2026"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    with SessionLocal() as db:
        user = User(username="ci39admin", password_hash=hash_password(PASSWORD), display_name="CI39 Admin", role="admin", is_active=True)
        customer = Customer(name="CI39 Cliente", code="CI39")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="CI39 Router",
            display_name="CI39 Router",
            device_identity="CI39-EDGE",
            serial_number="CI39SERIAL",
            primary_mac="02:39:AA:BB:CC:DD",
            management_ip="192.0.2.39",
            firmware_version="7.12.1",
            status="online",
            inventory_data={"agent_version": "0.20.0-legacy", "agent_transport": "legacy"},
        )
        db.add(device)
        db.flush()

        for idx in range(65):
            state = "pending" if idx < 2 else ("failed" if idx % 3 == 0 else "success")
            db.add(DeviceJob(device_id=device.id, job_type="diagnostic_ping" if idx % 2 == 0 else "diagnostic_logs", status=state, payload={"marker": f"CI39-JOB-{idx:02d}"}, result={"output": f"result-{idx}"}, last_error="CI39 simulated failure" if state == "failed" else None))
            db.add(AuditEvent(event_type="CI39_PING" if idx % 2 == 0 else "CI39_LOG", severity="warning" if idx % 3 == 0 else "info", customer_id=customer.id, device_id=device.id, actor_user_id=user.id, source="ci39", result="failed" if idx % 3 == 0 else "success", details={"marker": f"CI39-EVENT-{idx:02d}"}))
        db.commit()
        return customer.id, device.id


def login(client):
    page = client.get("/login")
    response = client.post("/login", data={"username": "ci39admin", "password": PASSWORD, "csrf": csrf_from(page.text)}, follow_redirects=False)
    assert response.status_code == 303


def main():
    customer_id, device_id = seed()
    client = TestClient(app)
    login(client)

    jobs = client.get(f"/devices/{device_id}/jobs", params={"per_page": 25})
    assert jobs.status_code == 200
    assert "Coda attiva" in jobs.text
    assert "65 risultati filtrati" in jobs.text
    assert ">1/3<" in jobs.text
    assert "diagnostic_ping" in jobs.text
    assert "diagnostic_logs" in jobs.text

    page3 = client.get(f"/devices/{device_id}/jobs", params={"per_page": 25, "page": 3})
    assert page3.status_code == 200
    assert ">3/3<" in page3.text

    # Search also inspects structured job payload/result, without dumping that
    # payload into the table itself.
    payload_search = client.get(f"/devices/{device_id}/jobs", params={"q": "CI39-JOB-64"})
    assert payload_search.status_code == 200
    assert "1 risultati filtrati" in payload_search.text

    failed = client.get(f"/devices/{device_id}/jobs", params={"status": "failed", "q": "CI39 simulated failure"})
    assert failed.status_code == 200
    assert "failed" in failed.text
    assert "CI39 simulated failure" in failed.text

    audit = client.get(f"/devices/{device_id}/jobs", params={"view": "audit", "event_type": "CI39_PING", "result": "success", "per_page": 25})
    assert audit.status_code == 200
    assert "CI39_PING" in audit.text
    assert "CI39_LOG" not in audit.text

    global_audit = client.get("/audit/events", params={"customer": str(customer_id), "device": "CI39SERIAL", "event_type": "CI39_LOG", "per_page": 25})
    assert global_audit.status_code == 200
    assert "Registro eventi" in global_audit.text
    assert "CI39_LOG" in global_audit.text
    assert "CI39_PING" not in global_audit.text
    assert "Esporta CSV" in global_audit.text

    exported = client.get("/audit/events/export.csv", params={"customer": str(customer_id), "device": "CI39SERIAL", "event_type": "CI39_LOG"})
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "CI39_LOG" in exported.text
    assert "CI39_PING" not in exported.text

    print("Core 0.39 activity worklists, pagination, filters and CSV export smoke passed")


if __name__ == "__main__":
    main()
