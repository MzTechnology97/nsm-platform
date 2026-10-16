"""ICMP thresholds: average RTT / loss over 30 minutes open and close an Action Center issue."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import icmp_monitor as icmp
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-ICMP-Thresholds-2026"


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        customer = Customer(name=f"CI ICMP thr {suffix}", code=f"IT{suffix[:6]}")
        db.add_all([customer, User(username=f"ci-it-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_cpe", name=f"TEST-IT-{suffix}", status="online", management_ip="198.51.100.80")
        quiet = Device(customer_id=customer.id, vendor="cambium", device_type="wireless_cpe", name=f"TEST-IT-Q-{suffix}", status="online", management_ip="198.51.100.81")
        db.add_all([cpe, quiet])
        db.commit()
        ids = (cpe.id, quiet.id)

    client = TestClient(app)
    page = client.get("/login").text
    client.post("/login", data={"username": f"ci-it-{suffix}", "password": PASSWORD, "csrf": re.search(r'name="csrf" value="([^"]+)"', page).group(1)})
    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}

    def icmp_settings(device_id, **body):
        return client.post(f"/api/ui/v1/devices/{device_id}/icmp", headers=headers, json=body)

    for bad in ({"rtt_ms": "abc"}, {"loss_pct": "150"}):
        refused = icmp_settings(ids[0], enabled=True, **bad)
        assert refused.status_code == 422 and refused.json()["detail"]["code"] == "threshold" and "Soglia non valida" in refused.text
    icmp_settings(ids[0], enabled=True, rtt_ms="50", loss_pct="10,5").raise_for_status()
    icmp_settings(ids[1], enabled=True).raise_for_status()  # no thresholds: never a quality issue
    with SessionLocal() as db:
        assert icmp.thresholds(db.get(Device, ids[0])) == {"rtt_ms": 50.0, "loss_pct": 10.5}
    page = client.get(f"/api/ui/v1/devices/{ids[0]}/icmp").json()
    assert page["rtt_ms"] == 50.0 and page["loss_pct"] == 10.5

    def issue(device_id):
        with SessionLocal() as db:
            return db.scalar(select(ActionIssue).where(ActionIssue.device_id == device_id, ActionIssue.category == "latency"))

    slow = {"sent": 3, "received": 3, "rtts": [80.0, 85.0, 90.0]}
    fast = {"sent": 3, "received": 3, "rtts": [18.0, 20.0, 22.0]}
    t0 = utcnow()
    for step in range(4):
        icmp.tick(now=t0 + timedelta(minutes=2 * step), prober=lambda host: slow)
    assert issue(ids[0]) is None, "fewer than 5 rounds: not judged yet"
    stats = icmp.tick(now=t0 + timedelta(minutes=8), prober=lambda host: slow)
    assert stats.get("threshold") == 1, stats
    opened = issue(ids[0])
    assert opened.status == "open" and opened.details["breaches"] == ["RTT medio 85.0 ms > 50 ms"], opened.details
    assert issue(ids[1]) is None
    # Back to normal: once the last 30 minutes are under the thresholds the issue closes.
    for step in range(5, 21):
        icmp.tick(now=t0 + timedelta(minutes=2 * step), prober=lambda host: fast)
    assert issue(ids[0]).status == "resolved"

    # Loss over the threshold also counts.
    lossy = {"sent": 3, "received": 2, "rtts": [20.0, 21.0]}
    for step in range(21, 37):
        icmp.tick(now=t0 + timedelta(minutes=2 * step), prober=lambda host: lossy)
    with SessionLocal() as db:
        latest = db.scalars(select(ActionIssue).where(ActionIssue.device_id == ids[0], ActionIssue.category == "latency", ActionIssue.status == "open")).one()
        assert latest.details["breaches"] == ["perdita 33.3% > 10.5%"], latest.details
    # Removing the thresholds closes it.
    icmp_settings(ids[0], enabled=True).raise_for_status()
    icmp.tick(now=t0 + timedelta(minutes=80), prober=lambda host: lossy)
    with SessionLocal() as db:
        assert not db.scalars(select(ActionIssue).where(ActionIssue.device_id == ids[0], ActionIssue.category == "latency", ActionIssue.status == "open")).all()
    print("ICMP thresholds smoke passed")


if __name__ == "__main__":
    main()
