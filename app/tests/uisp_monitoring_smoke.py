"""UBNT-02: UISP operational metrics normalized, sampled, shown and retained."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import uisp_connector as uisp
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, UispMetricSample, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password
from app.uisp_metrics import cleanup, extract, format_value
from app.uisp_sync import run_uisp_sync

PASSWORD = "CI-UISP-Monitoring-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    metrics = extract({"cpu": 12, "ram": 45.5, "signal": -61, "downlinkCapacity": 150000000, "uplinkCapacity": "80000000",
                       "uptime": 3 * 86400 + 3600, "frequency": 5180, "stationsCount": True, "temperature": 40})
    assert metrics == {"cpu_percent": 12.0, "ram_percent": 45.5, "signal_dbm": -61.0, "downlink_capacity_bps": 150000000,
                       "uplink_capacity_bps": 80000000, "uptime_seconds": 262800, "frequency_mhz": 5180.0}, metrics
    assert extract({"signal": 20, "cpu": 300}) == {}, "implausible values are dropped"
    assert extract(None) == {}
    assert format_value("downlink_capacity_bps", 150000000) == "150.0 Mbps" and format_value("uptime_seconds", 262800) == "3g 1h 0m"

    suffix = uuid.uuid4().hex[:6]
    uid = f"u-mon-{suffix}"
    state = {"signal": -61}

    def rows():
        return [{"identification": {"id": uid, "mac": "02:6b:00:00:00:01", "displayName": "TEST CPE monitor", "model": "LBE-5AC-Gen2", "role": "station"},
                 "overview": {"status": "active", "cpu": 12, "ram": 45.5, "signal": state["signal"], "downlinkCapacity": 150000000, "uptime": 262800}}]

    uisp._http_get = lambda url, token, verify_tls: rows()
    now = utcnow()
    with SessionLocal() as db:
        connection = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "uisp"))
        if not connection:
            connection = ConnectorIntegration(provider="uisp", name="UISP Network", base_url="https://uisp.example.test", secret_encrypted=encrypt_text("T"),
                                              is_enabled=True, verify_tls=True, settings={"api_version": "v2.1", "mode": "read_only"})
            db.add(connection)
        tech = User(username=f"ci-um-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)
        customer = Customer(name=f"CI UISP Mon {suffix}", code=f"UM{suffix}")
        db.add_all([tech, customer])
        db.flush()
        device = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name="TEST CPE monitor", primary_mac="02:6B:00:00:00:01",
                        external_device_id=uid, management_source="uisp", status="online")
        db.add(device)
        db.commit()
        ids = {"device": device.id, "customer": customer.id}

        run_uisp_sync(db, connection, now, trigger="test")
        db.commit()
        run_uisp_sync(db, connection, now + timedelta(minutes=2), trigger="test")
        db.commit()
        count = db.scalar(select(func.count(UispMetricSample.id)).where(UispMetricSample.device_id == ids["device"]))
        assert count == 1, "at most one sample every few minutes"
        state["signal"] = -70
        run_uisp_sync(db, connection, now + timedelta(minutes=6), trigger="test")
        db.commit()
        samples = list(db.scalars(select(UispMetricSample).where(UispMetricSample.device_id == ids["device"]).order_by(UispMetricSample.observed_at)))
        assert [s.signal_dbm for s in samples] == [-61.0, -70.0]
        assert db.get(Device, ids["device"]).inventory_data["uisp"]["metrics"]["signal_dbm"] == -70.0

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-um-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/devices/{ids['device']}/uisp").text
    for marker in ("Stato operativo da UISP", "-70 dBm", "-61 dBm", "150.0 Mbps", "3g 1h 0m", "non esposto da UISP"):
        assert marker in page, marker
    assert "segnale -70.0 dBm" in client.get(f"/customers/{ids['customer']}/devices").text

    with SessionLocal() as db:
        db.add(UispMetricSample(device_id=ids["device"], observed_at=utcnow() - timedelta(days=120), signal_dbm=-80))
        db.commit()
    assert cleanup() >= 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count(UispMetricSample.id)).where(UispMetricSample.device_id == ids["device"])) == 2
    print("UISP monitoring smoke passed")


if __name__ == "__main__":
    main()
