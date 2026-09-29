import uuid

from fastapi.testclient import TestClient

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

TEST_PASSWORD = "test-only-unified-config"
TEST_IDENTITY = "TEST-CCR-EDGE"
TEST_PRIMARY_MAC = "02:49:00:00:00:01"
TEST_DHCP_MAC = "02:49:00:00:00:35"
TEST_WAN_ADDRESS = "192.0.2.2/30"
TEST_WAN_GATEWAY = "192.0.2.1"


def seed():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        user = User(
            username=f"unified-{suffix}",
            password_hash=hash_password(TEST_PASSWORD),
            display_name="Unified Config Test",
            role="admin",
            is_active=True,
        )
        customer = Customer(name=f"Unified Config {suffix}", code=f"UC{suffix[:6]}")
        db.add_all([user, customer])
        db.flush()
        device = Device(
            customer_id=customer.id,
            vendor="mikrotik",
            device_type="router",
            name="Unified Test Router",
            display_name="Unified Test Router",
            device_identity=TEST_IDENTITY,
            model="CCR2004-16G-2S+",
            firmware_version="7.24.4 (stable)",
            serial_number=f"TEST-{suffix.upper()}",
            primary_mac=TEST_PRIMARY_MAC,
            management_ip="198.51.100.49",
            management_source="mikrotik_agent",
            status="online",
            last_seen=now,
            inventory_data={
                "agent_version": "0.49.2",
                "agent_transport": "modern",
                "last_source_ip": "198.51.100.49",
            },
        )
        db.add(device)
        db.flush()

        # Network addresses are RFC 5737 documentation ranges and MAC
        # addresses use the locally administered 02: prefix. No production
        # topology or observed hardware identifiers are stored in this fixture.
        snapshots = {
            "resources": {
                "identity": TEST_IDENTITY,
                "model": "CCR2004-16G-2S+",
                "routeros": "7.24.4 (stable)",
                "architecture": "arm64",
                "cpu": "ARM64",
                "cpu_count": 4,
                "cpu_load": 7,
                "free_memory": 3708588032,
                "total_memory": 4294967296,
                # Synthetic reproduction of the date-shaped uptime display bug.
                "uptime": "1970-01-02 02:56:32",
            },
            "interfaces": [
                {
                    "name": "ether1",
                    "type": "ether",
                    "running": True,
                    "disabled": False,
                    "mac-address": TEST_PRIMARY_MAC,
                    "actual-mtu": 1500,
                    "l2mtu": 1596,
                    "comment": "TEST-WAN",
                }
            ],
            "ip_addresses": [
                {
                    "address": TEST_WAN_ADDRESS,
                    "network": "192.0.2.0",
                    "interface": "sfp-sfpplus1",
                    "dynamic": False,
                    "disabled": False,
                    "invalid": False,
                    "comment": "TEST-WAN",
                }
            ],
            "routes": [
                {
                    "dst-address": "0.0.0.0/0",
                    "gateway": TEST_WAN_GATEWAY,
                    "distance": 1,
                    "routing-table": "main",
                    "active": True,
                    "dynamic": False,
                    "disabled": False,
                }
            ],
            "firewall": {
                "filter": [
                    {"chain": "input", "action": "accept", "protocol": "udp", "comment": "Synthetic VPN rule"}
                ],
                "nat": [
                    {"chain": "srcnat", "action": "masquerade", "src-address": "198.51.100.0/24", "comment": "TEST-NAT"}
                ],
            },
            "ppp_active": {
                "active": [],
                "sstp_clients": [
                    {
                        "name": "sstp-test1",
                        "running": True,
                        "disabled": False,
                        "connect-to": "vpn.example.test",
                        "user": "test-user",
                        "comment": "Synthetic SSTP",
                    }
                ],
                "l2tp_clients": [],
                "pppoe_clients": [],
                "pptp_clients": [],
                "ovpn_clients": [],
            },
            "dhcp_leases": [
                {
                    "address": "203.0.113.3",
                    "mac-address": TEST_DHCP_MAC,
                    "host-name": "test-host-01",
                    "server": "dhcp-test",
                    "status": "bound",
                    "dynamic": False,
                    "disabled": False,
                    "blocked": False,
                }
            ],
            "logs": [
                {"time": "13:11:00", "topics": "warning,system", "message": "Synthetic warning"}
            ],
        }
        for section, data in snapshots.items():
            db.add(
                DeviceJob(
                    device_id=device.id,
                    job_type="snapshot_section",
                    status="success",
                    payload={"section": section},
                    result={"section": section, "data": data},
                    created_at=now,
                    delivered_at=now,
                    completed_at=now,
                    attempts=1,
                )
            )
        db.commit()
        return user.username, device.id


def login(client, username):
    page = client.get("/login")
    import re
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    response = client.post(
        "/login",
        data={"username": username, "password": TEST_PASSWORD, "csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


def main():
    username, device_id = seed()
    client = TestClient(app)
    login(client, username)

    interfaces = client.get(f"/devices/{device_id}/configuration?section=interfaces")
    assert interfaces.status_code == 200
    assert "Interfacce" in interfaces.text and "ether1" in interfaces.text and "Totali" in interfaces.text
    assert "Aggiorna sezione" in interfaces.text and "Aggiorna tutto" in interfaces.text
    # No duplicated dedicated-page action or top-level Interface tab remains.
    assert f'href="/devices/{device_id}/interfaces"' not in interfaces.text

    addresses = client.get(f"/devices/{device_id}/configuration?section=ip_addresses")
    assert addresses.status_code == 200
    assert TEST_WAN_ADDRESS in addresses.text and "sfp-sfpplus1" in addresses.text

    routes = client.get(f"/devices/{device_id}/configuration?section=routes")
    assert routes.status_code == 200
    assert "0.0.0.0/0" in routes.text and TEST_WAN_GATEWAY in routes.text and "ACTIVE" in routes.text

    ppp = client.get(f"/devices/{device_id}/configuration?section=ppp_active")
    assert ppp.status_code == 200
    for marker in ("PPP / Tunnel", "sstp-test1", "SSTP", "vpn.example.test", "test-user", "UP"):
        assert marker in ppp.text, marker

    resources = client.get(f"/devices/{device_id}/configuration?section=resources")
    assert resources.status_code == 200
    assert "1d 02:56:32" in resources.text
    assert "1970-01-02 02:56:32" not in resources.text
    assert "3.5 GiB" in resources.text and "4.0 GiB" in resources.text

    old_interfaces = client.get(f"/devices/{device_id}/interfaces", follow_redirects=False)
    assert old_interfaces.status_code == 303
    assert f"/devices/{device_id}/configuration?section=interfaces" in old_interfaces.headers["location"]

    old_ip = client.get(f"/devices/{device_id}/ip-addresses?q=192.0.2", follow_redirects=False)
    assert old_ip.status_code == 303
    assert "section=ip_addresses" in old_ip.headers["location"] and "q=192.0.2" in old_ip.headers["location"]

    print("Unified MikroTik configuration workspace and PPP/tunnel UX smoke passed")


if __name__ == "__main__":
    main()
