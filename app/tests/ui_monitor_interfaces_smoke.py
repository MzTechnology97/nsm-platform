"""New interface, monitored interfaces of the Monitor tab: automatic choice, manual choice with limit, reset, permissions."""
import re
import uuid

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import AuditEvent, Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-UI-Monitor-Interfaces-2026"


def login(username):
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": username, "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    return client, {"X-CSRF-Token": client.get("/api/ui/v1/session").json()["csrf_token"]}


def main():
    suffix = uuid.uuid4().hex[:6]
    counters = {f"ether{n}": {"type": "ether", "rx_bps": 1000 * n, "tx_bps": 500 * n} for n in range(1, 11)}
    counters["pppoe-out1"] = {"type": "pppoe-out", "rx_bps": 9000, "tx_bps": 4000}
    with SessionLocal() as db:
        customer = Customer(name=f"CI UI Monitor {suffix}", code=f"MO{suffix}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-MO-RTR-{suffix}", status="online",
                        inventory_data={"interface_counters": counters})
        db.add(device)
        db.add_all([
            User(username=f"ci-mo-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True),
            User(username=f"ci-mo-a-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True),
        ])
        db.commit()
        device_id = device.id

    base = f"/api/ui/v1/devices/{device_id}/traffic-interfaces"
    assert TestClient(app).get(base).status_code == 401
    tech, csrf = login(f"ci-mo-{suffix}")
    auditor, auditor_csrf = login(f"ci-mo-a-{suffix}")

    data = auditor.get(base).json()
    assert data["max"] == 8 and data["custom"] is False and data["can_edit"] is False
    assert data["rows"][0]["name"] == "pppoe-out1" and data["rows"][0]["monitored"] and data["rows"][0]["wan"]
    assert sum(1 for row in data["rows"] if row["monitored"]) == 1

    assert auditor.post(base, headers=auditor_csrf, json={"interfaces": ["ether2"]}).status_code == 403
    assert tech.post(base, json={"interfaces": ["ether2"]}).status_code == 403, "CSRF header required"
    too_many = tech.post(base, headers=csrf, json={"interfaces": [f"ether{n}" for n in range(1, 10)]})
    assert too_many.status_code == 422 and too_many.json()["detail"]["code"] == "too_many"
    saved = tech.post(base, headers=csrf, json={"interfaces": ["ether2", "ether1", "ether2", "not-there"]}).json()
    assert saved["monitored"] == ["ether2", "ether1"]
    data = tech.get(base).json()
    assert data["custom"] is True and {row["name"] for row in data["rows"] if row["monitored"]} == {"ether1", "ether2"}
    assert tech.post(base, headers=csrf, json={"reset": True}).json()["monitored"] == ["pppoe-out1"]
    assert tech.get(base).json()["custom"] is False
    with SessionLocal() as db:
        assert db.query(AuditEvent).filter(AuditEvent.event_type == "DEVICE_TRAFFIC_INTERFACES_CHANGED", AuditEvent.device_id == device_id).count() == 2
    print("ui monitor interfaces smoke ok")


if __name__ == "__main__":
    main()
