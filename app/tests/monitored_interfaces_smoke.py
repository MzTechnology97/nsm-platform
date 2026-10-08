"""Every MikroTik interface can be monitored: robust per-interface collector, PPP sessions, grouped picker."""
import hashlib
import re
import secrets
import uuid

from fastapi.testclient import TestClient

from app import interface_traffic as traffic
from app import mikrotik_legacy
from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_routeros6 import validate_routeros6
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-Monitored-Interfaces-2026"


def main():
    for version, cap in (("7.16.2", 12000), ("7.12.1", 2400), ("6.49.18", 2400)):
        source, _, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=22), "CI22-secret", version)
        assert "running=yes && dynamic=no && type!=\"ether\"" not in source, "old collector replaced"
        lines = [line for line in source.splitlines() if line.startswith(":do { :foreach nsmIf in=[/interface find where") and "nsmIfaces" in line]
        assert len(lines) == 4, lines
        assert 'type="pppoe-out"' in lines[0] and 'dynamic=no && type="ether"]' in lines[1] and "dynamic=yes && running=yes" in lines[3]
        for line in lines:
            # Each interface is read in its own on-error: one failure never hides the others.
            assert line.count("on-error={}") == 2 and f"[:len $nsmIfaces] < {cap})" in line
        if version.startswith("6."):
            validate_routeros6(source)

    suffix = uuid.uuid4().hex[:8]
    raw = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        customer = Customer(name=f"CI Ifaces {suffix}", code=f"MI{suffix[:6]}")
        db.add_all([customer, User(username=f"ci-mi-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-MI-{suffix}", status="online", firmware_version="7.12.1",
                        inventory_data={"agent_transport": "legacy", "agent_privilege_profile": "legacy-ops-v1", "agent_version": "0.49.22-legacy"})
        db.add(device)
        db.flush()
        db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw.encode()).hexdigest(), is_active=True))
        db.commit()
        device_id = device.id

    ifaces = ("pppoe-out1|pppoe-out|1000|2000;ether1|ether|10|20;ether5|ether|0|0;vlan100|vlan|5|5;bridge-lan|bridge|50|60;"
              "wlan1|wlan|7|7;eoip-sede|eoip|1|1;<pppoe-mario>|pppoe-in|300|400;")
    client = TestClient(app)
    headers = {"X-NSM-Legacy-Transport": "headers-v1", "X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw,
               "X-NSM-Agent-Version": "0.49.22-legacy", "X-NSM-RouterOS": "7.12.1", "X-NSM-Ifaces": ifaces}
    assert client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers=headers).status_code == 200
    with SessionLocal() as db:
        rows = traffic.interface_rows(db.get(Device, device_id))
    groups = {row["name"]: row["group"] for row in rows}
    assert groups == {"pppoe-out1": "WAN", "ether1": "Ethernet", "ether5": "Ethernet", "vlan100": "VLAN", "bridge-lan": "Bridge",
                      "wlan1": "Wireless", "eoip-sede": "Tunnel", "<pppoe-mario>": "Sessione PPP"}, groups
    assert [row["name"] for row in rows][:3] == ["pppoe-out1", "ether1", "ether5"], "WAN monitored first, then Ethernet ports"

    page = client.get("/login").text
    client.post("/login", data={"username": f"ci-mi-{suffix}", "password": PASSWORD, "csrf": re.search(r'name="csrf" value="([^"]+)"', page).group(1)})
    monitor = client.get(f"/devices/{device_id}/monitor").text
    for name in ("ether5", "vlan100", "bridge-lan", "eoip-sede", "&lt;pppoe-mario&gt;"):
        assert f'value="{name}"' in monitor, name
    assert "Sessione PPP · pppoe-in" in monitor and "8 rilevate" in monitor
    token = re.search(r'name="csrf" value="([^"]+)"', monitor).group(1)
    client.post(f"/devices/{device_id}/traffic/interfaces", data={"csrf": token, "interface": ["ether1", "<pppoe-mario>", "vlan100"]})
    with SessionLocal() as db:
        assert db.get(Device, device_id).inventory_data["traffic_interfaces"] == ["ether1", "<pppoe-mario>", "vlan100"]
    print("Monitored interfaces smoke passed")


if __name__ == "__main__":
    main()
