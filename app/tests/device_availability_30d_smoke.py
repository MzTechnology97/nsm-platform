"""Device detail: 30-day ping availability (maintenance excluded) against the customer's SLA target or the 99% default."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.agent_models import DevicePingSample
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, MaintenanceWindow, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Availability-30d-2026"


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    start = now - timedelta(days=2)
    with SessionLocal() as db:
        db.add(User(username=f"ci-av-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
        strict = Customer(name=f"CI Availability {suffix}", code=f"AV{suffix[:6]}", sla_target_pct=99.9)
        plain = Customer(name=f"CI Availability plain {suffix}", code=f"AP{suffix[:6]}")
        db.add_all([strict, plain])
        db.flush()
        measured = Device(customer_id=strict.id, vendor="mikrotik", device_type="router", name=f"TEST-AV-{suffix}", status="online")
        silent = Device(customer_id=plain.id, vendor="mikrotik", device_type="router", name=f"TEST-AV-SILENT-{suffix}", status="online")
        db.add_all([measured, silent])
        db.flush()
        # 100 rounds, 10 consecutive without replies; a planned window covers 5 of those, and older samples are outside 30 days.
        for index in range(100):
            down = 40 <= index < 50
            db.add(DevicePingSample(device_id=measured.id, observed_at=start + timedelta(minutes=2 * index), target="192.0.2.30",
                                    sent=3, received=0 if down else 3))
        db.add(DevicePingSample(device_id=measured.id, observed_at=now - timedelta(days=40), target="192.0.2.30", sent=3, received=0))
        db.add(MaintenanceWindow(customer_id=strict.id, device_id=measured.id, starts_at=start + timedelta(minutes=80),
                                 ends_at=start + timedelta(minutes=90), reason="Test", created_at=now))
        db.commit()
        ids = {"measured": str(measured.id), "silent": str(silent.id)}

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-av-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303

    data = client.get(f"/api/ui/v1/devices/{ids['measured']}").json()["availability_30d"]
    # 95 rounds counted (5 in maintenance), 90 up.
    assert data["measured"] is True and data["availability"] == round(100 * 90 / 95, 3), data
    assert data["downtime_minutes"] == 10 and data["outages"] == 1 and data["target"] == 99.9 and data["customer_target"] is True, data

    silent = client.get(f"/api/ui/v1/devices/{ids['silent']}").json()["availability_30d"]
    assert silent == {"measured": False, "availability": None, "downtime_minutes": None, "outages": None, "target": 99.0, "customer_target": False}, silent
    print("Device availability 30d smoke passed")


if __name__ == "__main__":
    main()
