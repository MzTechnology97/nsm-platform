"""Syslog identification (2026-10-08): per-device key, strict mode, allowed networks, certain-or-discard."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mikrotik_syslog_config as cfg
from app import syslog_identity as identity
from app import syslog_receiver as rx
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, User, utcnow
from app.security import hash_password
from app.syslog_models import DeviceLogEntry

PASSWORD = "CI-Syslog-Identity-2026"
NAT_IP = "198.51.100.77"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def set_settings(**values):
    with SessionLocal() as db:
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == rx.PROVIDER))
        settings = {**rx.DEFAULTS, **values}
        if row is None:
            db.add(ConnectorIntegration(provider=rx.PROVIDER, name="Syslog integrato", base_url="syslog://nsm:514", secret_encrypted="", settings=settings))
        else:
            row.settings = settings
        db.commit()


def stored(device_id):
    with SessionLocal() as db:
        return [r.message for r in db.scalars(select(DeviceLogEntry).where(DeviceLogEntry.device_id == device_id).order_by(DeviceLogEntry.id))]


def main():
    key, rest = identity.extract_key("system,error,critical NSM-0123456789abcdef: login failure for user admin from 203.0.113.9 via winbox")
    assert key == "0123456789abcdef" and "NSM-" not in rest and rest.startswith("system,error,critical login failure")
    key, rest = identity.extract_key("NSM-0123456789abcdef system,info ether1 link up")
    assert key == "0123456789abcdef" and rest == "system,info ether1 link up"
    assert identity.extract_key("NSM-short message")[0] is None

    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    fresh = now.isoformat()
    stale = (now - timedelta(hours=2)).isoformat()
    with SessionLocal() as db:
        customer = Customer(name=f"CI Syslog Identity {suffix}", code=f"SI{suffix}")
        db.add(customer)
        db.flush()
        # Two MikroTik routers of the same customer behind the same public NAT address.
        r1 = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SI-R1-{suffix}", device_identity=f"r1-{suffix}",
                    management_ip=NAT_IP, status="online",
                    inventory_data={"agent_transport": "modern", "agent_version": "0.49.13", "management_ip_origin": "agent", "last_source_ip": NAT_IP, "last_heartbeat_at": fresh})
        r2 = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SI-R2-{suffix}", device_identity=f"r2-{suffix}",
                    management_ip=NAT_IP, status="online",
                    inventory_data={"agent_transport": "modern", "agent_version": "0.49.13", "management_ip_origin": "agent", "last_source_ip": NAT_IP, "last_heartbeat_at": fresh})
        # A CPE without agent behind the same NAT, recognisable only by its hostname.
        cpe = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-SI-CPE-{suffix}", management_ip=NAT_IP, status="online",
                     inventory_data={"syslog_key": "c0ffee0123456789"})
        # A router whose agent is silent: its old address may now belong to someone else.
        gone = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-SI-GONE-{suffix}", management_ip="203.0.113.99", status="offline",
                      inventory_data={"agent_transport": "modern", "agent_version": "0.49.13", "management_ip_origin": "agent", "last_source_ip": "203.0.113.99", "last_heartbeat_at": stale})
        db.add_all([r1, r2, cpe, gone])
        db.add(User(username=f"ci-si-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.flush()
        prefix1, prefix2 = cfg.expected_prefix(r1), cfg.expected_prefix(r2)
        assert prefix1 != prefix2 and re.fullmatch(r"NSM-[0-9a-f]{16}", prefix1)
        assert cfg.expected_prefix(Device(vendor="mikrotik", inventory_data={"agent_version": "0.49.12"})) is None, "older agents cannot carry the key"
        # Both routers confirmed the configuration with their key -> strict mode.
        for device, prefix in ((r1, prefix1), (r2, prefix2)):
            db.add(DeviceJob(device_id=device.id, job_type=cfg.JOB_TYPE, status="success", completed_at=now,
                             payload={"remote": "203.0.113.5", "port": 514, "topics": list(cfg.TOPICS), "prefix": prefix}))
        db.commit()
        ids = {"r1": r1.id, "r2": r2.id, "cpe": cpe.id, "gone": gone.id}
        keys = {"r1": prefix1, "r2": prefix2}
    assert cfg.auto_configure()["strict_changed"] >= 2

    set_settings(allowed_networks=["198.51.100.0/24", "192.0.2.0/24"], strict_mode=True)
    receiver = rx.Receiver()
    receiver.refresh()
    assert ids["r1"] in receiver.index.strict and ids["r2"] in receiver.index.strict

    # Shared NAT: each router recognised by its key; the CPE by its hostname; anything else discarded.
    receiver.handle(f"<28>Oct  8 09:00:00 r1 system,error,critical {keys['r1']} login failure for user admin from 203.0.113.9 via ssh".encode(), NAT_IP)
    receiver.handle(f"<30>Oct  8 09:00:01 r2 {keys['r2']} interface,info ether2 link down".encode(), NAT_IP)
    receiver.handle(f"<30>Oct  8 09:00:02 cpe-roof-{suffix}-NSM-c0ffee0123456789 kernel: wlan0 reconnect".encode(), NAT_IP)
    receiver.handle(b"<30>Oct  8 09:00:03 unknown-host kernel: who am i", NAT_IP)
    receiver.handle(b"<28>Oct  8 09:00:04 r1 system,error,critical login failure for user root from 203.0.113.9 via winbox", NAT_IP)
    receiver.handle(b"<30>Oct  8 09:00:05 x NSM-ffffffffffffffff forged with an invented key", NAT_IP)
    receiver.flush()
    assert stored(ids["r1"]) == ["login failure for user admin from 203.0.113.9 via ssh"], "the key is stripped from the stored line"
    assert stored(ids["r2"]) == ["ether2 link down"] and stored(ids["cpe"]) == ["wlan0 reconnect"], (stored(ids["r2"]), stored(ids["cpe"]), receiver.stats)
    rejected = receiver.stats["rejected"]
    assert rejected["missing_key"] == 2 and rejected["unknown_key"] == 1 and rejected["ambiguous"] == 0, rejected  # keyless lines are never attributed

    # WAN address change: the key follows the router, no heartbeat needed.
    receiver.handle(f"<30>Oct  8 09:01:00 r1 {keys['r1']} system,info pppoe-out1 connected".encode(), "198.51.100.200")
    receiver.flush()
    assert stored(ids["r1"])[-1] == "pppoe-out1 connected"

    # Strict mode: a keyless line from a strict router's address alone (no other candidate) is spoofing -> discarded.
    with SessionLocal() as db:
        db.get(Device, ids["cpe"]).management_ip = "192.0.2.10"
        db.commit()
    receiver.refresh()
    receiver.handle(b"<28>Oct  8 09:02:00 r1 system,error,critical login failure for user admin from 203.0.113.66 via winbox", NAT_IP)
    receiver.flush()
    assert receiver.stats["rejected"]["missing_key"] == 3 and len(stored(ids["r1"])) == 2

    # Outside the allowed networks: discarded before parsing, even with a valid key.
    receiver.handle(f"<30>Oct  8 09:03:00 r1 {keys['r1']} system,info hello".encode(), "203.0.113.50")
    # An address returned to the provider pool (agent silent for 2 hours) no longer points at our router.
    set_settings(allowed_networks=["198.51.100.0/24", "192.0.2.0/24", "203.0.113.0/24"], strict_mode=True)
    receiver.refresh()
    receiver.handle(b"<30>Oct  8 09:03:01 someone-else kernel: not our router", "203.0.113.99")
    receiver.flush()
    assert receiver.stats["rejected"]["network"] == 1 and receiver.stats["rejected"]["missing_key"] >= 4
    assert stored(ids["gone"]) == [], "stale heartbeat address: discarded, not attributed"

    # Without allowed networks, only addresses currently tied to a device are accepted.
    set_settings(allowed_networks=[], strict_mode=True)
    receiver.refresh()
    receiver.handle(b"<30>Oct  8 09:04:00 random kernel: hello", "192.0.2.123")
    assert receiver.stats["rejected"]["network"] == 2

    # Memory stays bounded under spoofed sources; per-device daily quota.
    rx.MAX_BUCKETS, rx.MAX_UNKNOWN_TRACKED, rx.DAILY_QUOTA = 50, 10, 3
    capped = rx.Receiver()
    set_settings(allowed_networks=["198.18.0.0/15", "198.51.100.0/24"], strict_mode=True)
    capped.refresh()
    for i in range(500):
        capped.handle(b"<30>spoof", f"198.18.{i // 250}.{i % 250 + 1}")
    assert len(capped.buckets) <= 50 and len(capped.unknown) <= 10
    for i in range(5):
        capped.handle(f"<30>Oct  8 09:05:00 r2 {keys['r2']} system,info line {i}".encode(), NAT_IP)
    assert capped.stats["rejected"]["quota"] == 2
    capped.queue.clear()
    capped.unknown.clear()

    # Agent: payload carries the key for 0.49.13; a router configured before the update is reconfigured once.
    with SessionLocal() as db:
        r2 = db.get(Device, ids["r2"])
        for job in db.scalars(select(DeviceJob).where(DeviceJob.device_id == ids["r2"], DeviceJob.job_type == cfg.JOB_TYPE)):
            job.payload = {**job.payload, "prefix": None}
        db.commit()
    set_settings(allowed_networks=[], strict_mode=True, public_host="203.0.113.5", auto_configure=True)
    stats = cfg.auto_configure()
    assert stats["queued"] >= 1
    with SessionLocal() as db:
        pending = db.scalar(select(DeviceJob).where(DeviceJob.device_id == ids["r2"], DeviceJob.job_type == cfg.JOB_TYPE, DeviceJob.status == "pending"))
        assert pending.payload["prefix"] == keys["r2"]
        assert db.get(Device, ids["r2"]).inventory_data["syslog_strict"] is False, "strict only after the router confirms the key"

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-si-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    assert "Solo righe con la chiave" in client.get(f"/devices/{ids['r1']}/logs").text
    page = client.get(f"/devices/{ids['cpe']}/logs").text
    assert "Hostname atteso" not in page and "NSM-c0ffee0123456789" in page, "key-only: no hostname attribution"
    admin_page = client.get("/admin/syslog").text
    assert "Reti consentite" in admin_page and "Nessuna rete consentita configurata" in admin_page and "Solo righe con chiave" in admin_page
    bad = client.post("/admin/syslog/settings", data={"csrf": csrf_from(admin_page), "allowed_networks": "198.51.100.0/24\nnot-a-network", "strict_mode": "1"}, follow_redirects=False)
    assert "bad_networks" in bad.headers["location"]
    good = client.post("/admin/syslog/settings", data={"csrf": csrf_from(admin_page), "allowed_networks": "198.51.100.0/24, 192.0.2.0/24", "strict_mode": "1"}, follow_redirects=False)
    assert "saved" in good.headers["location"]
    with SessionLocal() as db:
        assert rx.load_settings(db)["allowed_networks"] == ["198.51.100.0/24", "192.0.2.0/24"]
    print("Syslog identity smoke passed")


if __name__ == "__main__":
    main()
