"""2-minute Agent heartbeat and Cacti-style consolidation of telemetry older than 7 days."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app import mikrotik_agent as agent
from app import mikrotik_legacy
from app import telemetry_rollup
from app.agent_models import DeviceInterfaceSample, DeviceMetricSample
from app.db import SessionLocal
from app.entrypoint import app  # noqa: F401  (installs the source wrappers)
from app.mikrotik_heartbeat_interval import ENFORCE
from app.mikrotik_routeros6 import validate_routeros6
from app.models import Customer, Device


def main():
    assert agent.HEARTBEAT_INTERVAL_SECONDS == 120
    bootstrap = mikrotik_legacy._legacy_bootstrap_script("http://nsm.example.test", "T")
    assert "start-time=startup interval=2m" in bootstrap and "interval=5m" not in bootstrap
    for version in ("7.16.2", "7.12.1", "6.49.18"):
        source, transport, _ = mikrotik_legacy._select_agent_source("http://nsm.example.test", uuid.UUID(int=18), "CI18-secret", version)
        assert source.startswith(ENFORCE), (version, transport, source[:200])
        assert source.count('interval=[:totime "00:02:00"]') == 1
        if version.startswith("6."):
            validate_routeros6(source)

    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    old_start = (now - timedelta(days=8)).replace(minute=0, second=0, microsecond=0)
    recent_start = now - timedelta(days=2)
    with SessionLocal() as db:
        customer = Customer(name=f"CI Rollup {uuid.uuid4().hex[:6]}", code=f"RU{uuid.uuid4().hex[:6]}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-ROLLUP", status="online")
        db.add(device)
        db.flush()
        for start in (old_start, recent_start):
            for i in range(10):  # 20 minutes at 2-minute steps
                at = start + timedelta(minutes=2 * i)
                db.add(DeviceMetricSample(device_id=device.id, observed_at=at, cpu_load=float(i), free_memory_bytes=1000 + i, total_memory_bytes=4000, source="mikrotik_agent"))
                db.add(DeviceInterfaceSample(device_id=device.id, interface="pppoe-out1", if_type="pppoe-out", observed_at=at, rx_bytes=100 * i, tx_bytes=50 * i,
                                             rx_bps=1000.0 * i, tx_bps=10.0 * i))
        db.commit()
        device_id = device.id

    removed = telemetry_rollup.consolidate(now)
    assert removed == 16, removed  # 2 tables x (10 rows -> 2 slots)
    assert telemetry_rollup.consolidate(now) == 0, "idempotent"
    with SessionLocal() as db:
        old = list(db.scalars(select(DeviceMetricSample).where(DeviceMetricSample.device_id == device_id, DeviceMetricSample.observed_at < now - timedelta(days=7))
                              .order_by(DeviceMetricSample.observed_at)))
        assert [round(s.cpu_load, 1) for s in old] == [2.0, 7.0], [s.cpu_load for s in old]  # averages of 0..4 and 5..9
        assert [s.free_memory_bytes for s in old] == [1004, 1009], "the newest row of each slot is kept"
        traffic = list(db.scalars(select(DeviceInterfaceSample).where(DeviceInterfaceSample.device_id == device_id, DeviceInterfaceSample.observed_at < now - timedelta(days=7))
                                  .order_by(DeviceInterfaceSample.observed_at)))
        assert [(t.rx_bps, t.tx_bps, t.rx_bytes) for t in traffic] == [(2000.0, 20.0, 400), (7000.0, 70.0, 900)]
        recent = db.scalar(select(func.count()).select_from(DeviceMetricSample).where(DeviceMetricSample.device_id == device_id, DeviceMetricSample.observed_at >= now - timedelta(days=7)))
        assert recent == 10, "full resolution for the last 7 days"
    print("Heartbeat interval and telemetry rollup smoke passed")


if __name__ == "__main__":
    main()
