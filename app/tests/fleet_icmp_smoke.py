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
    attention = client.get(f"/operations/monitoring?customer={customer_id}").text
    assert "Non rispondono al ping" in attention and "Perdita pacchetti" in attention
    assert f"TEST-FI-DOWN-{suffix}" in attention and "non risponde" in attention
    assert f"TEST-FI-LOSS-{suffix}" in attention and "41.5 ms" in attention and "perdita 33%" in attention
    assert f"TEST-FI-IDLE-{suffix}" not in attention, "a device without ICMP issues needs no attention"
    only_down = client.get(f"/operations/monitoring?state=icmp_down&customer={customer_id}").text
    assert f"TEST-FI-DOWN-{suffix}" in only_down and f"TEST-FI-LOSS-{suffix}" not in only_down
    assert "attivo su 2 apparati, attivabile su altri 1 con IP di gestione" in attention

    token = re.search(r'name="csrf" value="([^"]+)"', attention).group(1)
    done = client.post("/operations/monitoring/icmp", data={"csrf": token, "customer": str(customer_id), "action": "enable"})
    assert "Monitoraggio ICMP attivato su 1 apparati." in done.text and "1 senza IP di gestione" in done.text
    with SessionLocal() as db:
        state = {key: bool((db.get(Device, value).inventory_data or {}).get("icmp_monitor", {}).get("enabled")) for key, value in ids.items()}
        assert state == {"down": True, "lossy": True, "idle": True, "no_ip": False, "foreign": False}, state
        assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == "ICMP_MONITOR_BULK", AuditEvent.customer_id == customer_id)) is not None
    client.post("/operations/monitoring/icmp", data={"csrf": token, "customer": str(customer_id), "action": "disable"})
    with SessionLocal() as db:
        assert not any((db.get(Device, ids[k]).inventory_data or {}).get("icmp_monitor", {}).get("enabled") for k in ("down", "lossy", "idle"))
        assert db.get(Device, ids["down"]).inventory_data["icmp_monitor"]["consecutive_losses"] == 0
    print("Fleet ICMP smoke passed")


if __name__ == "__main__":
    main()
