"""MTK-05: a diagnostic result (e.g. support snapshot) downloads as JSON evidence with its SHA-256 audited."""
import hashlib
import json
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Diagnostic-Export-2026"


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        customer = Customer(name=f"CI Export {suffix}", code=f"DX{suffix[:6]}")
        db.add_all([customer, User(username=f"ci-dx-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)])
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DX-{suffix}", device_identity="EDGE-DX",
                        status="online", firmware_version="7.12.1", inventory_data={"agent_transport": "legacy", "agent_version": "0.49.19-legacy"})
        other = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DX-O-{suffix}", status="online")
        db.add_all([device, other])
        db.flush()
        done = DeviceJob(device_id=device.id, job_type="support_snapshot", status="success", payload={}, completed_at=utcnow(),
                         result={"data": {"resources": {"identity": "EDGE-DX"}, "routes": [{"dst-address": "0.0.0.0/0"}]}, "legacy_transport": True})
        running = DeviceJob(device_id=device.id, job_type="support_snapshot", status="running", payload={})
        foreign = DeviceJob(device_id=other.id, job_type="diagnostic_logs", status="success", payload={}, result={"data": []})
        db.add_all([done, running, foreign])
        db.commit()
        ids = {"device": device.id, "done": done.id, "running": running.id, "foreign": foreign.id}

    client = TestClient(app)
    assert client.get(f"/devices/{ids['device']}/diagnostics/jobs/{ids['done']}/export.json").status_code == 401
    page = client.get("/login").text
    client.post("/login", data={"username": f"ci-dx-{suffix}", "password": PASSWORD, "csrf": re.search(r'name="csrf" value="([^"]+)"', page).group(1)})
    detail = client.get(f"/devices/{ids['device']}/diagnostics/jobs/{ids['done']}").text
    assert f"/diagnostics/jobs/{ids['done']}/export.json" in detail and "Scarica JSON" in detail
    response = client.get(f"/devices/{ids['device']}/diagnostics/jobs/{ids['done']}/export.json")
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"] and "support-snapshot-EDGE-DX" in response.headers["content-disposition"]
    assert response.headers["x-content-sha256"] == hashlib.sha256(response.content).hexdigest()
    document = json.loads(response.content)
    assert document["nsm_export"] == "diagnostic-v1" and document["device"]["identity"] == "EDGE-DX" and document["device"]["routeros"] == "7.12.1"
    assert document["job"]["type"] == "support_snapshot" and document["result"]["data"]["routes"][0]["dst-address"] == "0.0.0.0/0"
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "DIAGNOSTIC_EXPORTED", AuditEvent.device_id == ids["device"]))
        assert event is not None and event.details["sha256"] == response.headers["x-content-sha256"]
    assert client.get(f"/devices/{ids['device']}/diagnostics/jobs/{ids['running']}/export.json").status_code == 409
    assert client.get(f"/devices/{ids['device']}/diagnostics/jobs/{ids['foreign']}/export.json").status_code == 404, "job of another device"
    print("Diagnostic export smoke passed")


if __name__ == "__main__":
    main()
