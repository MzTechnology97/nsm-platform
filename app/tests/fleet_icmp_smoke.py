"""Fleet Monitoring: ICMP state per device (non risponde / perdita) and bulk enable/disable per customer."""
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-Fleet-ICMP-2026"


def main():
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        customer = Customer(name=f"CI Fleet ICMP {suffix}", code=f"FI{suffix[:6]}")
        other = Customer(name=f"CI Fleet ICMP other {suffix}", code=f"FO{suffix[:6]}")
        db.add_all([customer, other, User(username=f"ci-fi-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.flush()

        def device(name, owner=customer, ip=None, icmp=None):
            row = Device(customer_id=owner.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-FI-{name}-{suffix}", status="online",
                         management_ip=ip, inventory_data={"icmp_monitor": icmp} if icmp else {})
            db.add(row)
            return row

        down = device("DOWN", ip="198.51.100.61", icmp={"enabled": True, "consecutive_losses": 3, "last_loss": 100, "last_rtt": None})
        lossy = device("LOSS", ip="198.51.100.62", icmp={"enabled": True, "consecutive_losses": 0, "last_loss": 33, "last_rtt": 41.5})
        idle = device("IDLE", ip="198.51.100.63")
        no_ip = device("NOIP")
        foreign = device("FOREIGN", owner=other, ip="198.51.100.64")
        db.commit()
        ids = {key: row.id for key, row in {"down": down, "lossy": lossy, "idle": idle, "no_ip": no_ip, "foreign": foreign}.items()}
        customer_id = customer.id

    client = TestClient(app)
    page = client.get("/login").text
    client.post("/login", data={"username": f"ci-fi-{suffix}", "password": PASSWORD, "csrf": re.search(r'name="csrf" value="([^"]+)"', page).group(1)})
    api = "/api/ui/v1/monitoring"
    attention = client.get(api, params={"customer": str(customer_id)}).json()
    rows = {row["name"]: row for row in attention["items"]}
    assert attention["counts"]["icmp_down"] == 1 and attention["counts"]["packet_loss"] == 1
    assert rows[f"TEST-FI-DOWN-{suffix}"]["icmp"]["down"] is True
    lossy_row = rows[f"TEST-FI-LOSS-{suffix}"]["icmp"]
    assert lossy_row["last_rtt"] == 41.5 and lossy_row["last_loss"] == 33, lossy_row
    assert f"TEST-FI-IDLE-{suffix}" not in rows, "a device without ICMP issues needs no attention"
    only_down = [row["name"] for row in client.get(api, params={"state": "icmp_down", "customer": str(customer_id)}).json()["items"]]
    assert only_down == [f"TEST-FI-DOWN-{suffix}"]
    assert attention["counts"]["icmp_enabled"] == 2 and attention["counts"]["icmp_ready"] == 1

    headers = {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}
    done = client.post(f"{api}/icmp", headers=headers, json={"customer": str(customer_id), "enable": True}).json()
    assert done == {"enabled": True, "changed": 1, "skipped": 1}, done
    with SessionLocal() as db:
        state = {key: bool((db.get(Device, value).inventory_data or {}).get("icmp_monitor", {}).get("enabled")) for key, value in ids.items()}
        assert state == {"down": True, "lossy": True, "idle": True, "no_ip": False, "foreign": False}, state
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "ICMP_MONITOR_BULK", AuditEvent.customer_id == customer_id)) is not None
    client.post(f"{api}/icmp", headers=headers, json={"customer": str(customer_id), "enable": False}).raise_for_status()
    with SessionLocal() as db:
        assert not any((db.get(Device, ids[k]).inventory_data or {}).get("icmp_monitor", {}).get("enabled") for k in ("down", "lossy", "idle"))
        assert db.get(Device, ids["down"]).inventory_data["icmp_monitor"]["consecutive_losses"] == 0
    print("Fleet ICMP smoke passed")


if __name__ == "__main__":
    main()
