"""New interface, fleet Monitoring API: worklist states and counts, telemetry thresholds, ICMP bulk switch, permissions and scope."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent_models import DeviceMetricSample
from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UI-Monitoring-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Monitoring {suffix}", code=f"UM{suffix[:6]}")
        other = Customer(name=f"CI UI Monitoring other {suffix}", code=f"UN{suffix[:6]}")
        db.add_all([customer, other])
        db.flush()

        def device(name, owner=customer, status="online", ip=None, icmp=None):
            row = Device(customer_id=owner.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UM-{name}-{suffix}", status=status,
                         management_ip=ip, inventory_data={"icmp_monitor": icmp} if icmp else {})
            db.add(row)
            return row

        down = device("DOWN", ip="198.51.100.71", icmp={"enabled": True, "consecutive_losses": 3, "last_loss": 100})
        lossy = device("LOSS", ip="198.51.100.72", icmp={"enabled": True, "consecutive_losses": 0, "last_loss": 33, "last_rtt": 41.5})
        busy = device("BUSY", ip="198.51.100.73")
        offline = device("OFF", status="offline")
        fine = device("FINE", ip="198.51.100.74")
        foreign = device("FOREIGN", owner=other, status="offline", ip="198.51.100.75")
        db.flush()
        db.add(DeviceMetricSample(device_id=busy.id, observed_at=now - timedelta(minutes=5), cpu_load=93.0, free_memory_bytes=5_000_000, total_memory_bytes=100_000_000))
        db.add(DeviceMetricSample(device_id=fine.id, observed_at=now - timedelta(minutes=5), cpu_load=12.0, free_memory_bytes=60_000_000, total_memory_bytes=100_000_000))
        db.add_all([
            User(username=f"ci-um-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
            User(username=f"ci-um-au-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
            User(username=f"ci-um-scoped-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True, customer_scope=[str(other.id)]),
        ])
        db.commit()
        ids = {k: str(v.id) for k, v in dict(down=down, lossy=lossy, busy=busy, offline=offline, fine=fine, foreign=foreign, customer=customer, other=other).items()}

    base = f"/api/ui/v1/monitoring?customer={ids['customer']}"
    assert TestClient(app).get(base).status_code == 401
    client, headers = login(f"ci-um-{suffix}")
    body = client.get(base).json()
    names = {row["name"]: row for row in body["items"]}
    assert body["state"] == "attention" and set(names) == {f"TEST-UM-{n}-{suffix}" for n in ("DOWN", "LOSS", "BUSY", "OFF")}, sorted(names)
    busy = names[f"TEST-UM-BUSY-{suffix}"]
    assert set(busy["flags"]) == {"high_cpu", "low_memory"} and busy["cpu"] == 93.0 and busy["memory_free_percent"] == 5.0
    assert names[f"TEST-UM-DOWN-{suffix}"]["icmp"]["down"] and names[f"TEST-UM-LOSS-{suffix}"]["icmp"]["last_rtt"] == 41.5
    assert names[f"TEST-UM-OFF-{suffix}"]["customer"]["id"] == ids["customer"]
    counts = body["counts"]
    assert (counts["total"], counts["attention"], counts["offline"], counts["icmp_down"], counts["packet_loss"], counts["high_cpu"], counts["low_memory"]) == (5, 4, 1, 1, 1, 1, 1), counts
    assert counts["icmp_enabled"] == 2 and counts["icmp_ready"] == 2
    assert body["thresholds"] == {"cpu": 80.0, "memory": 10.0, "loss": 20, "stale_minutes": 15} and body["can_bulk"]
    assert [r["name"] for r in client.get(f"{base}&state=icmp_down").json()["items"]] == [f"TEST-UM-DOWN-{suffix}"]
    assert client.get(f"{base}&state=all").json()["total"] == 5
    assert client.get(f"{base}&state=bogus").json()["state"] == "attention"

    # Bulk ICMP: CSRF, only this customer, devices without IP skipped, audited.
    assert client.post("/api/ui/v1/monitoring/icmp", json={"customer": ids["customer"], "enable": True}).status_code == 403
    done = client.post("/api/ui/v1/monitoring/icmp", json={"customer": ids["customer"], "enable": True}, headers=headers).json()
    assert done == {"enabled": True, "changed": 2, "skipped": 1}, done
    with SessionLocal() as db:
        enabled = {k: bool((db.get(Device, uuid.UUID(ids[k])).inventory_data or {}).get("icmp_monitor", {}).get("enabled")) for k in ("busy", "fine", "offline", "foreign")}
        assert enabled == {"busy": True, "fine": True, "offline": False, "foreign": False}, enabled
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "ICMP_MONITOR_BULK", AuditEvent.customer_id == uuid.UUID(ids["customer"]))) is not None
    off = client.post("/api/ui/v1/monitoring/icmp", json={"customer": ids["customer"], "enable": False}, headers=headers).json()
    assert off["changed"] == 4 and off["enabled"] is False
    assert client.post("/api/ui/v1/monitoring/icmp", json={"customer": str(uuid.uuid4()), "enable": True}, headers=headers).status_code == 404

    auditor, au_headers = login(f"ci-um-au-{suffix}")
    assert auditor.get(base).json()["can_bulk"] is False
    assert auditor.post("/api/ui/v1/monitoring/icmp", json={"enable": True}, headers=au_headers).status_code == 403

    scoped, _ = login(f"ci-um-scoped-{suffix}")
    rows = scoped.get("/api/ui/v1/monitoring?state=all").json()["items"]
    assert all(row["customer"]["id"] == ids["other"] for row in rows) and any(row["id"] == ids["foreign"] for row in rows)
    assert client.get("/app/monitoring?state=offline").status_code == 200
    print("ui monitoring smoke ok")


if __name__ == "__main__":
    main()
