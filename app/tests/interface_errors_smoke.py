"""Interface errors and drops (MON-01): Agent counters, per-interval deltas, graph API and consolidation."""
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import interface_traffic as traffic
from app import mikrotik_legacy, telemetry_rollup
from app.agent_models import DeviceInterfaceSample
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_routeros6 import validate_routeros6
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Interface-Errors-2026"


def main():
    for version in ("7.16.2", "7.12.1", "6.49.18"):
        source, transport, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=20), "CI20-secret", version)
        assert ':local nsmIferrs "v1;"' in source and "[/interface get $nsmIf rx-drop]" in source
        if transport == "modern":
            assert '"iferrs"=$nsmIferrs;' in source and '"ifaces"=$nsmIfaces;' in source
        else:
            assert '",X-NSM-Iferr:" . [$nsmHeaderSafe $nsmIferrs]' in source
        if version.startswith("6."):
            validate_routeros6(source)

    assert traffic.parse_iferrs(None) is None and traffic.parse_iferrs("ether1|1|2|3|4;") is None, "no v1 prefix: old Agent"
    assert traffic.parse_iferrs("v1;") == {}
    assert traffic.parse_iferrs("v1;pppoe-out1|5|0|7|1;bad|x|1|1|1;") == {"pppoe-out1": (5, 0, 7, 1)}

    suffix = uuid.uuid4().hex[:8]
    t0 = utcnow() - timedelta(minutes=10)
    with SessionLocal() as db:
        customer = Customer(name=f"CI Iferr {suffix}", code=f"IE{suffix[:6]}")
        db.add_all([customer, User(username=f"ci-ie-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)])
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-IE-{suffix}", status="online", inventory_data={})
        old = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-IE-OLD-{suffix}", status="online", inventory_data={})
        db.add_all([device, old])
        db.flush()
        for row, errors in ((device, ("v1;", "v1;pppoe-out1|5|0|7|1;", "v1;pppoe-out1|9|1|7|1;")), (old, (None, None, None))):
            data = {}
            for step, err in enumerate(errors):
                ifaces = f"pppoe-out1|pppoe-out|{1000 * (step + 1) * 1000}|{500 * (step + 1) * 1000};ether1|ether|10|10;"
                traffic.record(db, row, data, ifaces, now=t0 + timedelta(minutes=2 * step), errors=err)
            row.inventory_data = data
        db.commit()
        samples = list(db.scalars(select(DeviceInterfaceSample).where(DeviceInterfaceSample.device_id == device.id).order_by(DeviceInterfaceSample.observed_at)))
        assert [(s.rx_errors, s.tx_errors, s.rx_drops, s.tx_drops) for s in samples] == [(5, 0, 7, 1), (4, 1, 0, 0)]
        assert device.inventory_data["interface_counters"]["pppoe-out1"]["err"] == [9, 1, 7, 1]
        assert all(s.rx_errors is None for s in db.scalars(select(DeviceInterfaceSample).where(DeviceInterfaceSample.device_id == old.id)))
        ids = (device.id, old.id)

    client = TestClient(app)
    page = client.get("/login").text
    client.post("/login", data={"username": f"ci-ie-{suffix}", "password": PASSWORD, "csrf": re.search(r'name="csrf" value="([^"]+)"', page).group(1)})
    data = client.get(f"/api/v1/devices/{ids[0]}/traffic?range=1h").json()
    assert data["stats"]["errors"] == {"supported": True, "rx_errors": 9, "tx_errors": 1, "rx_drops": 7, "tx_drops": 1}, data["stats"]
    assert [p["rx_errors"] for p in data["points"]] == [5, 4]
    assert client.get(f"/api/v1/devices/{ids[1]}/traffic?range=1h").json()["stats"]["errors"]["supported"] is False
    monitor = client.get(f"/devices/{ids[0]}/monitor").text
    assert "data-traffic-errors-chart" in monitor and "Errori e drop" in monitor

    # Consolidation sums the counts of a 10-minute slot.
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    start = (now - timedelta(days=8)).replace(minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        for i in range(5):
            db.add(DeviceInterfaceSample(device_id=ids[0], interface="pppoe-out1", if_type="pppoe-out", observed_at=start + timedelta(minutes=2 * i),
                                         rx_bps=100.0, tx_bps=10.0, rx_errors=i, tx_errors=1, rx_drops=None, tx_drops=0))
        db.commit()
    telemetry_rollup.consolidate(now)
    with SessionLocal() as db:
        row = db.scalars(select(DeviceInterfaceSample).where(DeviceInterfaceSample.device_id == ids[0], DeviceInterfaceSample.observed_at < now - timedelta(days=7))).one()
        assert (row.rx_errors, row.tx_errors, row.rx_drops, row.tx_drops, row.rx_bps) == (10, 5, None, 0, 100.0)
    print("Interface errors smoke passed")


if __name__ == "__main__":
    main()
