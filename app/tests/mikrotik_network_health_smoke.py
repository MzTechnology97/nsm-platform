import re
import uuid

from fastapi.testclient import TestClient

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

TEST_PASSWORD = "NetworkHealthA1"


def csrf(html):
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        user = User(username=f"nethealth-{suffix}", password_hash=hash_password(TEST_PASSWORD), display_name="Network Health", role="admin", is_active=True)
        customer = Customer(name=f"Network Health {suffix}", code=f"N46{suffix[:5]}")
        db.add_all([user, customer]); db.flush()
        modern = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="Modern Router", display_name="Modern CCR", status="online", inventory_data={"agent_transport":"modern","agent_version":"0.20.0"})
        legacy = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="Legacy Router", display_name="Legacy wAP", status="online", inventory_data={"agent_transport":"legacy","agent_version":"0.20.0-legacy"})
        db.add_all([modern, legacy]); db.flush()
        ip_rows = [
            {"address":"192.0.2.1/24","network":"192.0.2.0","interface":"ether1","actual-interface":"ether1","dynamic":False,"disabled":False,"invalid":False,"comment":"WAN static"},
            {"address":"10.0.0.2/24","network":"10.0.0.0","interface":"ether2","dynamic":True,"disabled":False,"invalid":False,"comment":"DHCP uplink"},
            {"address":"198.51.100.2/24","network":"198.51.100.0","interface":"ether3","dynamic":False,"disabled":True,"invalid":False,"comment":"Old backup"},
            {"address":"203.0.113.2/24","network":"203.0.113.0","interface":"ether4","dynamic":False,"disabled":False,"invalid":True,"comment":"Invalid test"},
        ]
        route_rows = [
            {"dst-address":"0.0.0.0/0","gateway":"192.0.2.254","distance":"1","routing-table":"main","active":True,"dynamic":False,"disabled":False,"check-gateway":"ping","comment":"Primary default"},
            {"dst-address":"10.10.0.0/16","gateway":"10.0.0.1","distance":"10","routing-table":"main","active":False,"dynamic":False,"disabled":False,"comment":"Backup route"},
            {"dst-address":"172.16.0.0/16","gateway":"10.0.0.3","distance":"1","routing-table":"main","active":False,"dynamic":False,"disabled":True,"comment":"Disabled route"},
            {"dst-address":"192.168.50.0/24","gateway":"ether5","distance":"0","routing-table":"main","active":True,"dynamic":True,"disabled":False,"comment":"Connected"},
        ]
        for device in (modern, legacy):
            db.add(DeviceJob(device_id=device.id, job_type="snapshot_section", status="success", payload={"section":"ip_addresses"}, result={"section":"ip_addresses","data":ip_rows}, created_at=now, completed_at=now))
            db.add(DeviceJob(device_id=device.id, job_type="snapshot_section", status="success", payload={"section":"routes"}, result={"section":"routes","data":route_rows}, created_at=now, completed_at=now))
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

    ips = client.get(f"/devices/{modern_id}/ip-addresses")
    assert ips.status_code == 200
    for marker in ("Modern CCR", "192.0.2.1/24", "10.0.0.2/24", "ether1", "WAN static", "Aggiorna snapshot"):
        assert marker in ips.text, marker
    dynamic = client.get(f"/devices/{modern_id}/ip-addresses?state=dynamic")
    assert dynamic.status_code == 200 and "10.0.0.2/24" in dynamic.text and "192.0.2.1/24" not in dynamic.text
    ip_search = client.get(f"/devices/{modern_id}/ip-addresses?q=Old+backup")
    assert ip_search.status_code == 200 and "198.51.100.2/24" in ip_search.text and "192.0.2.1/24" not in ip_search.text

    routes = client.get(f"/devices/{modern_id}/routes")
    assert routes.status_code == 200
    for marker in ("0.0.0.0/0", "192.0.2.254", "Primary default", "10.10.0.0/16", "Connected"):
        assert marker in routes.text, marker
    defaults = client.get(f"/devices/{modern_id}/routes?state=default")
    assert defaults.status_code == 200 and "0.0.0.0/0" in defaults.text and "10.10.0.0/16" not in defaults.text
    inactive = client.get(f"/devices/{modern_id}/routes?state=inactive")
    assert inactive.status_code == 200 and "10.10.0.0/16" in inactive.text and "172.16.0.0/16" not in inactive.text
    route_search = client.get(f"/devices/{modern_id}/routes?q=Connected")
    assert route_search.status_code == 200 and "192.168.50.0/24" in route_search.text and "Primary default" not in route_search.text

    legacy_ips = client.get(f"/devices/{legacy_id}/ip-addresses")
    legacy_routes = client.get(f"/devices/{legacy_id}/routes")
    for response in (legacy_ips, legacy_routes):
        assert response.status_code == 200
        assert "Transport LEGACY" in response.text
        assert "Aggiorna snapshot" not in response.text
    assert "192.0.2.1/24" in legacy_ips.text and "0.0.0.0/0" in legacy_routes.text

    print("Core 0.46 MikroTik IP and route health smoke passed")


if __name__ == "__main__":
    main()
