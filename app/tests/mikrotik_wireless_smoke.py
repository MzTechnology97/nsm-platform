"""MikroTik wireless signal (MON-01): runtime-parsed readers, aggregation per interface, API and Monitor panel."""
import hashlib
import re
import secrets
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_legacy
from app import mikrotik_wireless as wifi
from app.agent_models import DeviceAgentCredential, DeviceWirelessSample
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_routeros6 import validate_routeros6
from app.models import Customer, Device, User
from app.security import hash_password

PASSWORD = "CI-Wireless-Signal-2026"


def main():
    for version in ("7.16.2", "7.12.1", "6.49.18"):
        source, transport, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=21), "CI21-secret", version)
        # Menus of packages that may be missing are compiled at run time only.
        assert '[:parse ":global nsmWifiG; :foreach r in=[/interface wireless registration-table find]' in source
        assert "\\$r" in source and '\\"|\\"' in source and ":local nsmWifiP1 [:parse" in source
        if transport == "modern":
            assert '"wifi"=$nsmWifi;' in source and "/interface wifi registration-table" in source
        else:
            assert '",X-NSM-Wifi:" . [$nsmHeaderSafe $nsmWifi]' in source
        if version.startswith("6."):
            assert "/interface wifi " not in source, "no wifi package on RouterOS 6"
            validate_routeros6(source)
        elif version.startswith("7.12"):
            assert "/interface wifi registration-table" in source

    assert wifi.parse(None) is None and wifi.parse("wlan1|-60|90;") is None
    assert wifi.parse("v1;") == {}
    parsed = wifi.parse("v1;wlan1|-62dBm@6Mbps|85;wlan1|-70@HT20-7|60;wifi1|-55|;bad|abc|1;wlan1|-300|1;")
    assert parsed["wlan1"] == {"clients": 2, "signal_min": -70.0, "signal_avg": -66.0, "signal_max": -62.0, "ccq_avg": 72.5}
    assert parsed["wifi1"] == {"clients": 1, "signal_min": -55.0, "signal_avg": -55.0, "signal_max": -55.0, "ccq_avg": None}
    assert "bad" not in parsed

    suffix = uuid.uuid4().hex[:8]
    raw = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        customer = Customer(name=f"CI Wireless {suffix}", code=f"WL{suffix[:6]}")
        db.add_all([customer, User(username=f"ci-wl-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="wireless_cpe", name=f"TEST-WL-{suffix}", status="online", firmware_version="7.12.1",
                        inventory_data={"agent_transport": "legacy", "agent_privilege_profile": "legacy-ops-v1", "agent_version": "0.49.21-legacy"})
        db.add(device)
        db.flush()
        db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", secret_hash=hashlib.sha256(raw.encode()).hexdigest(), is_active=True))
        db.commit()
        device_id = device.id

    client = TestClient(app)
    headers = {"X-NSM-Legacy-Transport": "headers-v1", "X-NSM-Device-ID": str(device_id), "X-NSM-Device-Secret": raw,
               "X-NSM-Agent-Version": "0.49.21-legacy", "X-NSM-RouterOS": "7.12.1", "X-NSM-Ifaces": "wlan1|wlan|100|200;"}
    assert client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers=headers).status_code == 200  # older payload: no wifi header
    for signal in ("-61dBm@6Mbps", "-63dBm@6Mbps"):
        assert client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers={**headers, "X-NSM-Wifi": f"v1;wlan1|{signal}|90;"}).status_code == 200
    with SessionLocal() as db:
        samples = list(db.scalars(select(DeviceWirelessSample).where(DeviceWirelessSample.device_id == device_id).order_by(DeviceWirelessSample.observed_at)))
        assert [(s.interface, s.clients, s.signal_avg, s.ccq_avg) for s in samples] == [("wlan1", 1, -61.0, 90.0), ("wlan1", 1, -63.0, 90.0)]
        assert db.get(Device, device_id).inventory_data["wireless"]["interfaces"]["wlan1"]["signal_avg"] == -63.0

    page = client.get("/login").text
    client.post("/login", data={"username": f"ci-wl-{suffix}", "password": PASSWORD, "csrf": re.search(r'name="csrf" value="([^"]+)"', page).group(1)})
    data = client.get(f"/api/v1/devices/{device_id}/wireless?range=1h").json()
    assert data["interface"] == "wlan1" and data["sample_count"] == 2 and data["current"]["signal_avg"] == -63.0
    assert [p["signal_avg"] for p in data["points"]] == [-61.0, -63.0]
    monitor = client.get(f"/devices/{device_id}/monitor").text
    assert "Segnale wireless" in monitor and "wireless_monitor.js" in monitor and '<option value="wlan1">' in monitor
    print("MikroTik wireless smoke passed")


if __name__ == "__main__":
    main()
