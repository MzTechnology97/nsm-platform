import re

from fastapi.testclient import TestClient

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

    jobs_api = f"/api/ui/v1/devices/{device_id}/jobs"
    jobs = client.get(jobs_api)
    assert jobs.status_code == 200, jobs.text
    first = jobs.json()
    assert first["total"] == 65 and "active" in first
    assert "diagnostic_ping" in jobs.text and "diagnostic_logs" in jobs.text

    last = client.get(jobs_api, params={"offset": 60}).json()
    assert last["offset"] == 60 and len(last["items"]) == 5

    # Search also inspects structured job payload/result.
    payload_search = client.get(jobs_api, params={"q": "CI39-JOB-64"}).json()
    assert payload_search["total"] == 1

    failed = client.get(jobs_api, params={"status": "failed", "q": "CI39 simulated failure"})
    assert failed.status_code == 200
    assert failed.json()["total"] >= 1 and all(item["status"] == "failed" for item in failed.json()["items"])

    # 33 even-indexed PING events exist; 11 are multiples of 6 and failed,
    # therefore the event_type + success filter must return exactly 22 rows.
    audit = client.get("/api/ui/v1/audit/events", params={"customer": str(customer_id), "event_type": "CI39_PING", "result": "success"}).json()
    assert audit["total"] == 22 and all(item["event_type"] == "CI39_PING" for item in audit["items"])

    # There are exactly 32 odd-indexed LOG events: the global audit log (new interface API) filters them.
    filters = {"customer": str(customer_id), "device": "CI39SERIAL", "event_type": "CI39_LOG"}
    global_audit = client.get("/api/ui/v1/audit/events", params=filters)
    assert global_audit.status_code == 200, global_audit.text
    page = global_audit.json()
    assert page["total"] == 32 and all(item["event_type"] == "CI39_LOG" for item in page["items"]), page["total"]
    # The classic address opens the same view in the new interface, filters included.
    moved = client.get("/audit/events", params=filters, follow_redirects=False)
    assert moved.status_code == 302 and moved.headers["location"].startswith("/app/events?") and "event_type=CI39_LOG" in moved.headers["location"]

    exported = client.get("/audit/events/export.csv", params={"customer": str(customer_id), "device": "CI39SERIAL", "event_type": "CI39_LOG"})
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "CI39_LOG" in exported.text
    assert "CI39_PING" not in exported.text

    print("Core 0.39 activity worklists, pagination, filters and CSV export smoke passed")


if __name__ == "__main__":
    main()
