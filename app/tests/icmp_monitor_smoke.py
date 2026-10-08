"""ICMP latency/loss from NSM: settings, worker rounds, unreachable issue, API, panel, consolidation."""
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import icmp_monitor as icmp
from app import icmp_probe, telemetry_rollup
from app.agent_models import DevicePingSample
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-ICMP-Monitor-2026"


def csrf_from(text):
    return re.search(r'name="csrf" value="([^"]+)"', text).group(1)


def main():
    # Real probe: works where unprivileged ping sockets are allowed, otherwise reports why.
    try:
        result = icmp_probe.ping("127.0.0.1", count=2, timeout=1.0, interval=0.05)
        assert result["sent"] == 2 and result["received"] == 2 and all(r >= 0 for r in result["rtts"]), result
    except icmp_probe.IcmpUnavailable as exc:
        assert "ICMP non consentito" in str(exc)
    assert icmp.valid_target("192.0.2.10") == "192.0.2.10" and icmp.valid_target("2001:db8::1") == "2001:db8::1"
    for bad in ("127.0.0.1", "0.0.0.0", "169.254.1.1", "224.0.0.1", "nsm.example.test", ""):
        assert icmp.valid_target(bad) is None, bad

    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        customer = Customer(name=f"CI ICMP {suffix}", code=f"IC{suffix[:6]}")
        db.add_all([customer, User(username=f"ci-icmp-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.flush()
        cpe = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-ICMP-CPE-{suffix}", status="online", management_ip="198.51.100.40")
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-ICMP-RTR-{suffix}", status="online", inventory_data={})
        db.add_all([cpe, router])
        db.commit()
        cpe_id, router_id = cpe.id, router.id

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-icmp-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)},
                       follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{cpe_id}").text
    assert "Latenza e perdita (ICMP da NSM)" in page and 'placeholder="198.51.100.40"' in page
    token = csrf_from(page)
    # A router without management IP needs an explicit address; bad addresses are refused.
    assert "non ha un IP di gestione" in client.post(f"/devices/{router_id}/latency/settings", data={"csrf": token, "enabled": "1"}).text
    assert "Indirizzo non valido" in client.post(f"/devices/{router_id}/latency/settings", data={"csrf": token, "enabled": "1", "target": "127.0.0.1"}).text
    client.post(f"/devices/{router_id}/latency/settings", data={"csrf": token, "enabled": "1", "target": "203.0.113.7", "back": f"/devices/{router_id}/monitor"})
    client.post(f"/devices/{cpe_id}/latency/settings", data={"csrf": token, "enabled": "1"})

    replies = {"198.51.100.40": {"sent": 3, "received": 3, "rtts": [10.0, 12.0, 14.0]}, "203.0.113.7": {"sent": 3, "received": 0, "rtts": []}}
    t0 = utcnow()
    for step in range(3):
        stats = icmp.tick(now=t0 + timedelta(minutes=2 * step), prober=lambda host: replies[host])
        assert stats["probed"] == 2, stats
    assert icmp.tick(now=t0 + timedelta(minutes=4, seconds=30), prober=lambda host: replies[host])["probed"] == 0, "one round every 2 minutes"
    with SessionLocal() as db:
        samples = list(db.scalars(select(DevicePingSample).where(DevicePingSample.device_id == cpe_id)))
        assert len(samples) == 3 and samples[0].rtt_avg == 12.0 and samples[0].rtt_min == 10.0 and samples[0].received == 3
        issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == router_id, ActionIssue.category == "reachability", ActionIssue.status == "open"))
        assert issue is not None and issue.details["target"] == "203.0.113.7"
        assert db.scalar(select(ActionIssue).where(ActionIssue.device_id == cpe_id, ActionIssue.category == "reachability")) is None
    # The router answers again: the issue closes.
    replies["203.0.113.7"] = {"sent": 3, "received": 2, "rtts": [30.0, 50.0]}
    icmp.tick(now=t0 + timedelta(minutes=6), prober=lambda host: replies[host])
    with SessionLocal() as db:
        assert db.scalar(select(ActionIssue).where(ActionIssue.device_id == router_id, ActionIssue.category == "reachability")).status == "resolved"

    data = client.get(f"/api/v1/devices/{router_id}/latency?range=1h").json()
    assert data["target"] == "203.0.113.7" and data["sample_count"] == 4
    assert data["stats"]["loss"] == round(100 * (1 - 2 / 12), 2) and data["stats"]["rtt_avg"] == 40.0
    assert [p["loss"] for p in data["points"]] == [100.0, 100.0, 100.0, 33.3]
    monitor = client.get(f"/devices/{router_id}/monitor").text
    assert "data-latency-root" in monitor and "latency_monitor.js" in monitor

    # Ping sockets not allowed: nothing stored, the reason is shown on the device.
    def denied(host):
        raise icmp_probe.IcmpUnavailable("ICMP non consentito dal sistema (test).")
    assert icmp.tick(now=t0 + timedelta(minutes=20), prober=denied)["unavailable"] is True
    assert "Ping non eseguibile dal server" in client.get(f"/devices/{cpe_id}").text

    # Disabling stops the rounds.
    client.post(f"/devices/{cpe_id}/latency/settings", data={"csrf": token})
    assert icmp.tick(now=t0 + timedelta(minutes=40), prober=lambda host: replies[host])["probed"] == 1

    # Consolidation after 7 days: RTT averaged, packets summed.
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    start = (now - timedelta(days=8)).replace(minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        for i in range(5):
            db.add(DevicePingSample(device_id=cpe_id, observed_at=start + timedelta(minutes=2 * i), target="198.51.100.40", sent=3, received=3 - (i == 0),
                                    rtt_min=1.0, rtt_avg=float(10 + i), rtt_max=20.0))
        db.commit()
    telemetry_rollup.consolidate(now)
    with SessionLocal() as db:
        row = db.scalars(select(DevicePingSample).where(DevicePingSample.device_id == cpe_id, DevicePingSample.observed_at < now - timedelta(days=7))).one()
        assert (row.sent, row.received, row.rtt_avg) == (15, 14, 12.0)
    print("ICMP monitor smoke passed")


if __name__ == "__main__":
    main()
