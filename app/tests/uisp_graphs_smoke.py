"""UBNT-08 step 1: UISP history as chart series (signal, capacity, CPU/RAM, stations) on the device UISP tab."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app import uisp_metrics
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, UispMetricSample, User, utcnow
from app.security import hash_password

PASSWORD = "CI-UISP-Graphs-2026"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI UISP Graphs {suffix}", code=f"UG{suffix}")
        db.add(customer)
        db.flush()
        device = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-UG-{suffix}", primary_mac="02:6B:00:00:0A:01",
                        external_device_id=f"u-graph-{suffix}", management_source="uisp", status="online",
                        inventory_data={"uisp": {"metrics": {"signal_dbm": -58.0}, "metrics_at": now.isoformat()}})
        db.add(device)
        db.flush()
        for i in range(60):
            if 20 <= i < 30:
                continue  # one-hour outage: the chart must show a hole
            db.add(UispMetricSample(device_id=device.id, observed_at=now - timedelta(minutes=5 * (60 - i)), signal_dbm=-60.0 + (i % 5),
                                    cpu_percent=10.0 + i % 7, ram_percent=40.0, downlink_capacity_bps=150_000_000, uplink_capacity_bps=80_000_000))
        db.add(User(username=f"ci-ug-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
        db.commit()
        device_id = device.id
        data = uisp_metrics.series(db, device, "24h")
    ids = [c["id"] for c in data["charts"]]
    assert ids == ["signal", "capacity", "resources"], "charts without data (stations) are omitted"
    signal = data["charts"][0]["series"][0]["points"]
    assert data["sample_count"] == 50 and any(v is None for _t, v in signal), "outage is a hole"
    assert all(-61 <= v <= -55 for _t, v in signal if v is not None)
    capacity = data["charts"][1]
    assert [s["label"] for s in capacity["series"]] == ["Downlink", "Uplink"] and capacity["unit"] == "bps"
    try:
        uisp_metrics.series(None, None, "1y")
        raise AssertionError("invalid range accepted")
    except ValueError:
        pass
    with SessionLocal() as db:
        many = [UispMetricSample(device_id=device_id, observed_at=now - timedelta(minutes=i), cpu_percent=1.0) for i in range(2000)]
        assert len(uisp_metrics._bucket(sorted(many, key=lambda s: s.observed_at), ["cpu_percent"])) <= uisp_metrics.MAX_POINTS

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-ug-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    api = client.get(f"/api/v1/devices/{device_id}/uisp-metrics?range=7d")
    assert api.status_code == 200 and [c["id"] for c in api.json()["charts"]] == ["signal", "capacity", "resources"]
    assert client.get(f"/api/v1/devices/{device_id}/uisp-metrics?range=bad").status_code == 400
    response = client.get(f"/devices/{device_id}/uisp")
    page = response.text
    assert response.status_code == 200, (response.status_code, page[:300])
    assert "data-series-root" in page and "series_chart.js" in page and "Grafici UISP" in page
    assert TestClient(app).get(f"/api/v1/devices/{device_id}/uisp-metrics").status_code == 401
    print("UISP graphs smoke passed")


if __name__ == "__main__":
    main()
