import re
import uuid

from fastapi.testclient import TestClient

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

TEST_PASSWORD = "PolicyHealthA1"


def csrf(html):
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        user = User(username=f"policyhealth-{suffix}", password_hash=hash_password(TEST_PASSWORD), display_name="Policy Health", role="admin", is_active=True)
        customer = Customer(name=f"Policy Health {suffix}", code=f"P47{suffix[:5]}")
        db.add_all([user, customer]); db.flush()
        modern = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="Modern Router", display_name="Modern CCR", status="online", inventory_data={"agent_transport":"modern","agent_version":"0.49.2"})
        legacy = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="Legacy Router", display_name="Legacy wAP", status="online", inventory_data={"agent_transport":"legacy","agent_version":"0.49.2-legacy"})
        db.add_all([modern, legacy]); db.flush()
        firewall = {
            "filter": [
                {"chain":"input","action":"accept","protocol":"tcp","dst-port":"8291","src-address":"192.0.2.0/24","disabled":False,"comment":"Winbox management"},
                {"chain":"forward","action":"drop","protocol":"tcp","dst-port":"23","disabled":False,"comment":"Block telnet"},
                {"chain":"input","action":"drop","protocol":"udp","disabled":True,"comment":"Old disabled rule"},
            ],
            "nat": [
                {"chain":"srcnat","action":"masquerade","out-interface":"ether1","disabled":False,"comment":"Internet NAT"},
            ],
        }
        leases = [
            {"address":"198.51.100.10","mac-address":"02:47:00:00:00:01","host-name":"office-pc","server":"dhcp-office","status":"bound","dynamic":True,"blocked":False,"disabled":False,"expires-after":"9m20s","last-seen":"40s","comment":"Reception"},
            {"address":"198.51.100.20","mac-address":"02:47:00:00:00:02","host-name":"printer","server":"dhcp-office","status":"waiting","dynamic":False,"blocked":False,"disabled":False,"comment":"Static printer"},
            {"address":"198.51.100.99","mac-address":"02:47:00:00:00:99","host-name":"blocked-host","server":"dhcp-office","status":"bound","dynamic":True,"blocked":True,"disabled":False,"comment":"Quarantine"},
        ]
        for device in (modern, legacy):
            db.add(DeviceJob(device_id=device.id, job_type="snapshot_section", status="success", payload={"section":"firewall"}, result={"section":"firewall","data":firewall}, created_at=now, completed_at=now))
            db.add(DeviceJob(device_id=device.id, job_type="snapshot_section", status="success", payload={"section":"dhcp_leases"}, result={"section":"dhcp_leases","data":leases}, created_at=now, completed_at=now))
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

    old_firewall = client.get(f"/devices/{modern_id}/firewall", follow_redirects=False)
    old_dhcp = client.get(f"/devices/{modern_id}/dhcp-leases", follow_redirects=False)
    assert old_firewall.status_code == 303 and "section=firewall" in old_firewall.headers["location"]
    assert old_dhcp.status_code == 303 and "section=dhcp_leases" in old_dhcp.headers["location"]

    firewall = client.get(f"/devices/{modern_id}/configuration?section=firewall")
    assert firewall.status_code == 200
    for marker in ("Modern CCR", "Winbox management", "Block telnet", "Internet NAT", "8291", "Aggiorna sezione"):
        assert marker in firewall.text, marker
    nat = client.get(f"/devices/{modern_id}/configuration?section=firewall&kind=nat")
    assert nat.status_code == 200 and "Internet NAT" in nat.text and "Winbox management" not in nat.text
    disabled = client.get(f"/devices/{modern_id}/configuration?section=firewall&state=disabled")
    assert disabled.status_code == 200 and "Old disabled rule" in disabled.text and "Block telnet" not in disabled.text
    drop = client.get(f"/devices/{modern_id}/configuration?section=firewall&action=drop")
    assert drop.status_code == 200 and "Block telnet" in drop.text and "Internet NAT" not in drop.text
    search = client.get(f"/devices/{modern_id}/configuration?section=firewall&q=8291")
    assert search.status_code == 200 and "Winbox management" in search.text and "Block telnet" not in search.text

    dhcp = client.get(f"/devices/{modern_id}/configuration?section=dhcp_leases")
    assert dhcp.status_code == 200
    for marker in ("office-pc", "02:47:00:00:00:01", "dhcp-office", "Reception", "blocked-host"):
        assert marker in dhcp.text, marker
    bound = client.get(f"/devices/{modern_id}/configuration?section=dhcp_leases&state=bound")
    assert bound.status_code == 200 and "office-pc" in bound.text and "printer" not in bound.text and "blocked-host" not in bound.text
    blocked = client.get(f"/devices/{modern_id}/configuration?section=dhcp_leases&state=blocked")
    assert blocked.status_code == 200 and "blocked-host" in blocked.text and "office-pc" not in blocked.text
    dhcp_search = client.get(f"/devices/{modern_id}/configuration?section=dhcp_leases&q=printer")
    assert dhcp_search.status_code == 200 and "Static printer" in dhcp_search.text and "Reception" not in dhcp_search.text

    legacy_fw = client.get(f"/devices/{legacy_id}/configuration?section=firewall")
    legacy_dhcp = client.get(f"/devices/{legacy_id}/configuration?section=dhcp_leases")
    for response in (legacy_fw, legacy_dhcp):
        assert response.status_code == 200
        assert "Aggiorna sezione" not in response.text
    assert "Block telnet" in legacy_fw.text and "office-pc" in legacy_dhcp.text

    print("Core 0.47 MikroTik firewall and DHCP health smoke passed in unified workspace")


if __name__ == "__main__":
    main()
