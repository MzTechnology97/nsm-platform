"""MON-01: interface traffic counters in the agent heartbeat, bit/s history and Cacti-style graphs."""
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import interface_traffic as traffic
from app import mikrotik_agent as agent
from app import mikrotik_legacy
from app.agent_models import DeviceAgentCredential, DeviceInterfaceSample, DeviceMetricSample
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, User
from app.mikrotik_routeros6 import validate_routeros6
from app.mikrotik_telemetry import telemetry_cleanup
from app.security import hash_password

PASSWORD = "CI-Traffic-2026"
SECRET = "test-only-traffic-secret"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def ifaces(pppoe_rx, pppoe_tx, ether_rx=10_000, ether_tx=20_000):
    return f"pppoe-out1|pppoe-out|{pppoe_rx}|{pppoe_tx};ether1|ether|{ether_rx}|{ether_tx};bridge-lan|bridge|5|6;"


def age_counters(device_id, seconds):
    """Pretend the previous heartbeat happened ``seconds`` ago."""
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        data = dict(device.inventory_data or {})
        counters = {}
        for name, value in data["interface_counters"].items():
            at = datetime.fromisoformat(value["at"]) - timedelta(seconds=seconds)
            counters[name] = {**value, "at": at.isoformat()}
        data["interface_counters"] = counters
        device.inventory_data = data
        db.commit()


def unit_checks():
    parsed = traffic.parse_ifaces("wan|x|ether|1|2;broken;bad|ether|x|1;big|ether|" + str(2**64) + "|1;ok|lte|3|4;")
    assert parsed == {"wan|x": ("ether", 1, 2), "ok": ("lte", 3, 4)}, parsed
    assert traffic.percentile(list(range(1, 101))) == 95
    assert traffic.format_bps(12_500_000) == "12.50 Mbit/s" and traffic.format_bps(None) == "—"
    assert traffic._rate(1000, 500, 300) is None, "counter reset gives a gap, not a negative rate"
    assert traffic.monitored_interfaces({}, {"ether1": ("ether", 1, 1), "ether2": ("ether", 1, 1)}) == ["ether1"]
    assert traffic.monitored_interfaces({"traffic_interfaces": []}, {"lte1": ("lte", 1, 1)}) == []
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = [DeviceInterfaceSample(observed_at=start + timedelta(minutes=5 * i), rx_bps=float(i), tx_bps=None) for i in range(1500)]
    points = traffic._buckets(rows)
    assert len(points) == traffic.MAX_POINTS and points[-1]["rx_max"] == 1499.0 and points[0]["tx_bps"] is None
    gapped = rows[:100] + rows[160:]
    points = traffic._buckets(gapped)
    holes = [p for p in points if p["rx_bps"] is None]
    assert len(points) <= traffic.MAX_POINTS and len(holes) == 1, "an outage is a hole, not an average"
    assert rows[99].observed_at.isoformat() < holes[0]["timestamp"] < rows[160].observed_at.isoformat()
    stats = traffic._stats(rows[:3], "rx_bps")
    assert stats["max"] == 2.0 and stats["bytes"] == int((0 + 1 + 2) * 300 / 8)


def agent_sources():
    base, device_id = "http://nsm.example.test", uuid.UUID(int=91)
    modern, _, _ = mikrotik_legacy._select_agent_source(base, device_id, "CI91-secret", "7.24.4")
    assert '"ifaces"=$nsmIfaces' in modern and "rx-byte" in modern
    legacy, _, _ = mikrotik_legacy._select_agent_source(base, device_id, "CI91-secret", "7.12.1")
    assert ",X-NSM-Ifaces:\" . [$nsmHeaderSafe $nsmIfaces]" in legacy and ":serialize" not in legacy
    v6, _, _ = mikrotik_legacy._select_agent_source(base, device_id, "CI91-secret", "6.49.10")
    validate_routeros6(v6)
    assert "X-NSM-Ifaces" in v6 and "dynamic=yes && running=yes" in v6
    # WAN-like interfaces are collected first so they survive the length cap.
    assert modern.index('type="pppoe-out"') < modern.index('dynamic=no && type="ether"]'), "WAN clients first, so they fit the size cap"


def main():
    unit_checks()
    agent_sources()
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI Traffic {suffix}", code=f"TR{suffix}")
        db.add(customer)
        db.flush()
        modern = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-TR-{suffix}", status="online",
                        management_source="mikrotik_agent", firmware_version="7.24.4", inventory_data={"agent_transport": "modern", "agent_version": "0.49.9"})
        legacy = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-TR-L-{suffix}", status="online",
                        management_source="mikrotik_agent", firmware_version="7.12.1", inventory_data={"agent_transport": "legacy", "agent_version": "0.49.9-legacy", "legacy_agent": True})
        db.add_all([modern, legacy])
        db.flush()
        for device in (modern, legacy):
            db.add(DeviceAgentCredential(device_id=device.id, agent_type="mikrotik_agent", secret_hash=agent._secret_digest(SECRET), is_active=True))
        db.add_all([User(username=f"ci-tr-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                    User(username=f"ci-tr-a-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)])
        db.commit()
        modern_id, legacy_id = modern.id, legacy.id

    client = TestClient(app, base_url="http://192.0.2.81")
    auth = {"X-NSM-Device-ID": str(modern_id), "X-NSM-Device-Secret": SECRET}

    def beat(rx, tx):
        body = {"inventory": {"identity": f"TEST-TR-{suffix}", "routeros_version": "7.24.4"}, "agent_version": "0.49.9",
                "metrics": {"cpu_load": "5", "free_memory": "100MiB", "uptime": "1d", "ifaces": ifaces(rx, tx)}}
        response = client.post("/api/v1/agents/mikrotik/heartbeat", headers=auth, json=body)
        assert response.status_code == 200, response.text

    beat(1_000_000, 2_000_000)
    with SessionLocal() as db:
        device = db.get(Device, modern_id)
        assert "ifaces" not in device.inventory_data["metrics"], "counter string stays out of the 200-char metrics copy"
        assert set(device.inventory_data["interface_counters"]) == {"pppoe-out1", "ether1", "bridge-lan"}
        assert not db.scalars(select(DeviceInterfaceSample).where(DeviceInterfaceSample.device_id == modern_id)).all(), "first heartbeat has no rate"

    age_counters(modern_id, 300)
    beat(1_000_000 + 37_500_000, 2_000_000 + 3_750_000)  # 1 Mbit/s in, 100 kbit/s out over 300 s
    with SessionLocal() as db:
        rows = db.scalars(select(DeviceInterfaceSample).where(DeviceInterfaceSample.device_id == modern_id)).all()
        assert [r.interface for r in rows] == ["pppoe-out1"], "only WAN interfaces are stored by default"
        assert abs(rows[0].rx_bps - 1_000_000) < 20_000 and abs(rows[0].tx_bps - 100_000) < 2_000, (rows[0].rx_bps, rows[0].tx_bps)
        assert rows[0].rx_bytes == 38_500_000

    age_counters(modern_id, 300)
    beat(10, 10)  # PPPoE reconnect: counters restart from zero
    with SessionLocal() as db:
        assert len(db.scalars(select(DeviceInterfaceSample).where(DeviceInterfaceSample.device_id == modern_id)).all()) == 1, "reset makes a gap"

    admin = TestClient(app, base_url="http://192.0.2.81")
    assert admin.post("/login", data={"username": f"ci-tr-{suffix}", "password": PASSWORD, "csrf": csrf_from(admin.get("/login").text)}, follow_redirects=False).status_code == 303
    data = admin.get(f"/api/v1/devices/{modern_id}/traffic?range=24h").json()
    assert data["interface"] == "pppoe-out1" and data["sample_count"] == 1 and data["interfaces"][0] == "pppoe-out1"
    assert abs(data["stats"]["rx"]["p95"] - 1_000_000) < 20_000 and data["stats"]["tx"]["max"] < 110_000
    assert admin.get(f"/api/v1/devices/{modern_id}/traffic?range=2y").status_code == 400

    monitor = admin.get(f"/devices/{modern_id}/monitor")
    assert monitor.status_code == 200 and "data-traffic-root" in monitor.text and "traffic_monitor.js" in monitor.text
    assert monitor.text.index("rrd_chart.js") < monitor.text.index("traffic_monitor.js")
    assert 'value="pppoe-out1" checked' in monitor.text and 'value="ether1">' in monitor.text

    page = admin.get(f"/devices/{modern_id}/monitor").text
    saved = admin.post(f"/devices/{modern_id}/traffic/interfaces", data={"csrf": csrf_from(page), "interface": ["ether1", "pppoe-out1", "not-there"]}, follow_redirects=False)
    assert saved.status_code == 303 and "traffic_saved" in saved.headers["location"]
    with SessionLocal() as db:
        assert db.get(Device, modern_id).inventory_data["traffic_interfaces"] == ["ether1", "pppoe-out1"]
    age_counters(modern_id, 300)
    beat(20_010, 20_010)
    with SessionLocal() as db:
        names = sorted(r.interface for r in db.scalars(select(DeviceInterfaceSample).where(DeviceInterfaceSample.device_id == modern_id)))
        assert names == ["ether1", "pppoe-out1", "pppoe-out1"], names
    reset = admin.post(f"/devices/{modern_id}/traffic/interfaces", data={"csrf": csrf_from(page), "reset": "1"}, follow_redirects=False)
    assert reset.status_code == 303
    with SessionLocal() as db:
        assert "traffic_interfaces" not in db.get(Device, modern_id).inventory_data

    auditor = TestClient(app, base_url="http://192.0.2.81")
    assert auditor.post("/login", data={"username": f"ci-tr-a-{suffix}", "password": PASSWORD, "csrf": csrf_from(auditor.get("/login").text)}, follow_redirects=False).status_code == 303
    assert auditor.get(f"/api/v1/devices/{modern_id}/traffic").status_code == 200
    apage = auditor.get(f"/devices/{modern_id}/monitor").text
    assert "Salva interfacce" not in apage
    assert auditor.post(f"/devices/{modern_id}/traffic/interfaces", data={"csrf": csrf_from(apage), "interface": "ether1"}).status_code == 403
    assert TestClient(app).get(f"/api/v1/devices/{modern_id}/traffic").status_code == 401

    legacy_headers = {"X-NSM-Legacy-Transport": "headers-v1", "X-NSM-Device-ID": str(legacy_id), "X-NSM-Device-Secret": SECRET,
                      "X-NSM-Agent-Version": "0.49.9-legacy", "X-NSM-RouterOS": "7.12.1", "X-NSM-CPU-Load": "3", "X-NSM-Ifaces": "lte1|lte|100|200;"}
    assert client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers=legacy_headers, content=b"").status_code == 200
    age_counters(legacy_id, 300)
    legacy_headers["X-NSM-Ifaces"] = "lte1|lte|3750100|375200;"
    assert client.post("/api/v1/agents/mikrotik/heartbeat-legacy", headers=legacy_headers, content=b"").status_code == 200
    with SessionLocal() as db:
        row = db.scalar(select(DeviceInterfaceSample).where(DeviceInterfaceSample.device_id == legacy_id))
        assert row is not None and row.interface == "lte1" and abs(row.rx_bps - 100_000) < 2_000

    with SessionLocal() as db:
        db.add(DeviceInterfaceSample(device_id=modern_id, interface="pppoe-out1", observed_at=datetime.now(timezone.utc) - timedelta(days=traffic.RETENTION_DAYS + 1), rx_bps=1.0))
        old = datetime.now(timezone.utc) - timedelta(days=traffic.RETENTION_DAYS + 30)
        # MON-02: an offline device keeps its last telemetry forever.
        db.add_all([DeviceInterfaceSample(device_id=legacy_id, interface="old-wan", observed_at=old, rx_bps=7.0),
                    DeviceInterfaceSample(device_id=legacy_id, interface="old-wan", observed_at=old - timedelta(minutes=5), rx_bps=6.0),
                    DeviceMetricSample(device_id=legacy_id, observed_at=old, cpu_load=9.0)])
        db.commit()
    assert traffic.cleanup() >= 2
    with SessionLocal() as db:
        kept = db.scalars(select(DeviceInterfaceSample.rx_bps).where(DeviceInterfaceSample.device_id == legacy_id, DeviceInterfaceSample.interface == "old-wan")).all()
        assert kept == [7.0], kept
        assert db.scalars(select(DeviceInterfaceSample).where(DeviceInterfaceSample.device_id == modern_id, DeviceInterfaceSample.observed_at < old + timedelta(days=29))).all() == []
    offline = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        lone = Device(customer_id=db.get(Device, legacy_id).customer_id, vendor="mikrotik", device_type="router", name=f"TEST-TR-OFF-{offline}", status="offline")
        db.add(lone)
        db.flush()
        db.add_all([DeviceMetricSample(device_id=lone.id, observed_at=old, cpu_load=11.0), DeviceMetricSample(device_id=lone.id, observed_at=old - timedelta(hours=1), cpu_load=12.0)])
        db.commit()
        lone_id = lone.id
    telemetry_cleanup()
    with SessionLocal() as db:
        assert db.scalars(select(DeviceMetricSample.cpu_load).where(DeviceMetricSample.device_id == lone_id)).all() == [11.0], "last CPU/memory sample of an offline device is kept"
    print("Interface traffic smoke passed")


if __name__ == "__main__":
    main()
