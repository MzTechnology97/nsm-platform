"""INV-07: agent-managed MikroTik devices get a management IP and a LAN IP in the device list."""
import re
import uuid

from fastapi.testclient import TestClient

from app import agent_addresses as addresses
from app import mikrotik_agent as agent
from app import mikrotik_legacy
from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.mikrotik_routeros6 import validate_routeros6
from app.security import hash_password

PASSWORD = "CI-Mgmt-IP-2026"
SECRET = "test-only-mgmt-ip-secret"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def unit_checks():
    rows = addresses.parse_addresses("198.51.100.1/24|bridge;203.0.113.7/32|pppoe-out1;bad|x;192.0.2.77|ether2;")
    assert [r["address"] for r in rows] == ["198.51.100.1", "203.0.113.7", "192.0.2.77"] and rows[1]["interface"] == "pppoe-out1"
    assert addresses.scope_label("203.0.113.7") == "privato", "RFC 5737 documentation ranges are not global"
    assert addresses.scope_label("8.8.8.8") == "pubblico" and addresses.scope_label("100.64.1.1") == "CGNAT"


def sources():
    base, device_id = "http://nsm.example.test", uuid.UUID(int=92)
    modern, _, _ = mikrotik_legacy._select_agent_source(base, device_id, "CI92-secret", "7.24.4")
    assert '"addresses"=$nsmAddrs' in modern and "/ip address find where disabled=no" in modern
    legacy, _, _ = mikrotik_legacy._select_agent_source(base, device_id, "CI92-secret", "7.12.1")
    assert ",X-NSM-Addrs:\" . [$nsmHeaderSafe $nsmAddrs]" in legacy
    v6, _, _ = mikrotik_legacy._select_agent_source(base, device_id, "CI92-secret", "6.49.10")
    validate_routeros6(v6)


def main():
    unit_checks()
    sources()
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI Mgmt IP {suffix}", code=f"MI{suffix}")
        db.add(customer)
        db.flush()
        modern = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-MI-{suffix}", status="online",
                        management_source="mikrotik_agent", inventory_data={"agent_transport": "modern"})
        manual = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-MI-M-{suffix}", status="online",
                        management_source="mikrotik_agent", management_ip="198.51.100.99", inventory_data={"agent_transport": "modern"})
        legacy = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-MI-L-{suffix}", status="online",
                        management_source="mikrotik_agent", inventory_data={"agent_transport": "legacy", "legacy_agent": True})
        db.add_all([modern, manual, legacy])
        db.flush()
        for device in (modern, manual, legacy):
            db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", secret_hash=agent._secret_digest(SECRET), is_active=True))
        db.add(User(username=f"ci-mi-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.commit()
        ids = {"modern": modern.id, "manual": manual.id, "legacy": legacy.id, "customer": customer.id}

    # The heartbeat arrives from the documentation address 192.0.2.50 (the test client host).
    client = TestClient(app, base_url="http://nsm.example.test", client=("192.0.2.50", 50000))

    def beat(device_id, addrs):
        body = {"inventory": {"identity": f"TEST-MI-{suffix}"}, "agent_version": "0.49.10",
                "metrics": {"cpu_load": "1", "addresses": addrs}}
        response = client.post("/api/v1/agents/mikrotik/heartbeat", headers={"X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": SECRET}, json=body)
        assert response.status_code == 200, response.text

    # Old agents (no addresses): the address NSM sees becomes the management IP.
    beat(ids["modern"], None)
    with SessionLocal() as db:
        device = db.get(Device, ids["modern"])
        source = device.inventory_data["last_source_ip"]
        assert source == "192.0.2.50" and device.management_ip == source and device.inventory_data["management_ip_origin"] == "agent", (device.management_ip, source)

    # Agent 0.49.10: a public WAN address wins, the bridge address is the LAN IP.
    beat(ids["modern"], "198.51.100.1/24|bridge;8.8.4.4/32|pppoe-out1;")
    with SessionLocal() as db:
        device = db.get(Device, ids["modern"])
        assert device.management_ip == "8.8.4.4" and device.inventory_data["lan_ip"] == "198.51.100.1"
        assert "addresses" not in device.inventory_data["metrics"]

    # A management IP typed by an operator is never overwritten.
    beat(ids["manual"], "198.51.100.2/24|bridge;")
    with SessionLocal() as db:
        device = db.get(Device, ids["manual"])
        assert device.management_ip == "198.51.100.99" and device.inventory_data["lan_ip"] == "198.51.100.2"

    legacy_headers = {"X-NSM-Legacy-Transport": "headers-v1", "X-NSM-Device-ID": str(ids["legacy"]), "X-NSM-Device-Secret": SECRET,
                      "X-NSM-Agent-Version": "0.49.10-legacy", "X-NSM-RouterOS": "7.12.1", "X-NSM-Addrs": "203.0.113.10/24|bridge;"}
    assert client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers=legacy_headers, content=b"").status_code == 200
    with SessionLocal() as db:
        device = db.get(Device, ids["legacy"])
        assert device.inventory_data["lan_ip"] == "203.0.113.10" and device.management_ip == device.inventory_data["last_source_ip"]

    admin = TestClient(app, base_url="http://192.0.2.50")
    assert admin.post("/login", data={"username": f"ci-mi-{suffix}", "password": PASSWORD, "csrf": csrf_from(admin.get("/login").text)}, follow_redirects=False).status_code == 303
    listing = admin.get(f"/customers/{ids['customer']}/devices").text
    assert "<code>8.8.4.4</code><small>LAN 198.51.100.1</small>" in listing, "device list shows management and LAN IP"
    devices = admin.get(f"/devices?q=TEST-MI-{suffix}").text
    assert "8.8.4.4" in devices and "pubblico · LAN 198.51.100.1" in devices
    page = admin.get(f"/devices/{ids['modern']}").text
    assert "IP LAN" in page and "198.51.100.1" in page and "Visto da NSM" in page
    print("Agent management IP smoke passed")


if __name__ == "__main__":
    main()
