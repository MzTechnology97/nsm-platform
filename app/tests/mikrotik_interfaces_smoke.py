import json
import re
import uuid

from fastapi.testclient import TestClient

from app.mikrotik_agent_generation import TARGET_AGENT_VERSION as CURRENT_AGENT
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

TEST_PASSWORD = "InterfaceHealthA1"


def csrf(html):
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        user = User(username=f"ifhealth-{suffix}", password_hash=hash_password(TEST_PASSWORD), display_name="Interface Health", role="admin", is_active=True)
        customer = Customer(name=f"Interface Health {suffix}", code=f"I45{suffix[:5]}")
        db.add_all([user, customer]); db.flush()
        modern = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="Modern Router", display_name="Modern CCR", status="online", inventory_data={"agent_transport":"modern","agent_version":CURRENT_AGENT})
        legacy = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="Legacy Router", display_name="Legacy wAP", status="online", inventory_data={"agent_transport":"legacy","agent_version":CURRENT_AGENT + "-legacy"})
        db.add_all([modern, legacy]); db.flush()
        rows = [
            {"name":"ether1","type":"ether","running":True,"disabled":False,"mac-address":"AA:BB:CC:45:00:01","actual-mtu":"1500","l2mtu":"1592","rx-byte":"1048576","tx-byte":"2097152","comment":"WAN"},
            {"name":"ether2","type":"ether","running":False,"disabled":False,"mac-address":"AA:BB:CC:45:00:02","actual-mtu":"1500","comment":"Backup"},
            {"name":"wlan1","type":"wlan","running":False,"disabled":True,"mac-address":"AA:BB:CC:45:00:03","actual-mtu":"1500","comment":"Disabled radio"},
        ]
        for device in (modern, legacy):
            db.add(DeviceJob(device_id=device.id, job_type="snapshot_section", status="success", payload={"section":"interfaces"}, result={"section":"interfaces","data":rows}, created_at=now, completed_at=now))
        db.commit()
        return user.username, modern.id, legacy.id


def login(client, username):
    page = client.get("/login")
    response = client.post("/login", data={"username":username,"password":TEST_PASSWORD,"csrf":csrf(page.text)}, follow_redirects=False)
    assert response.status_code == 303


def main():
    username, modern_id, legacy_id = seed()
    client = TestClient(app)
    login(client, username)

    def interfaces(device_id, query=""):
        response = client.get(f"/api/ui/v1/devices/{device_id}/configuration?section=interfaces{query}")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["can_refresh"] is True and data["online"] is True
        return json.dumps(data["view"], ensure_ascii=False)

    old = client.get(f"/devices/{modern_id}/interfaces", follow_redirects=False)
    assert old.status_code == 302 and old.headers["location"] == f"/app/devices/{modern_id}/configuration"
    page = interfaces(modern_id)
    for marker in ("ether1", "ether2", "wlan1", "AA:BB:CC:45:00:01", "1.0 MiB", "2.0 MiB", "WAN"):
        assert marker in page, marker
    down = interfaces(modern_id, "&state=down")
    assert "ether2" in down and "ether1" not in down and "wlan1" not in down
    search = interfaces(modern_id, "&q=45%3A00%3A03")
    assert "wlan1" in search and "ether1" not in search

    assert "ether1" in interfaces(legacy_id), "legacy agents read interfaces through the allow-listed transport"

    print("Core 0.45 MikroTik interface health smoke passed")


if __name__ == "__main__":
    main()
