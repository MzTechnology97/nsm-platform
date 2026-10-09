"""MON-01: graphs aggregated per customer and per site (WAN traffic sum, reporting devices, CPU, radio signal)."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app import customer_graphs as cg
from app.agent_models import DeviceInterfaceSample, DeviceMetricSample, DevicePingSample, DeviceWirelessSample
from app.db import SessionLocal
from app.entrypoint import app
from app.models import Customer, Device, Site, UispMetricSample, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Customer-Graphs-2026"
WAN = {"pppoe-out1": {"type": "pppoe-out"}, "ether2": {"type": "ether"}}


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI Graphs {suffix}", code=f"CG{suffix}")
        other = Customer(name=f"CI Graphs other {suffix}", code=f"CO{suffix}")
        db.add_all([customer, other])
        db.flush()
        north, south = Site(customer_id=customer.id, name=f"Nord {suffix}"), Site(customer_id=customer.id, name=f"Sud {suffix}")
        foreign = Site(customer_id=other.id, name=f"Estranea {suffix}")
        db.add_all([north, south, foreign])
        db.flush()
        r1 = Device(customer_id=customer.id, site_id=north.id, vendor="mikrotik", device_type="router", name=f"TEST-CG-R1-{suffix}", status="online",
                    inventory_data={"interface_counters": WAN})
        r2 = Device(customer_id=customer.id, site_id=south.id, vendor="mikrotik", device_type="router", name=f"TEST-CG-R2-{suffix}", status="offline",
                    inventory_data={"interface_counters": WAN})
        cpe = Device(customer_id=customer.id, site_id=north.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-CG-U-{suffix}", status="online")
        db.add_all([r1, r2, cpe])
        db.flush()
        for minutes in range(5, 120, 5):
            at = now - timedelta(minutes=minutes)
            db.add(DeviceInterfaceSample(device_id=r1.id, interface="pppoe-out1", observed_at=at, rx_bps=40e6, tx_bps=5e6))
            db.add(DeviceInterfaceSample(device_id=r1.id, interface="ether2", observed_at=at, rx_bps=900e6, tx_bps=900e6))  # LAN: not monitored
            if minutes > 60:  # r2 went offline an hour ago
                db.add(DeviceInterfaceSample(device_id=r2.id, interface="pppoe-out1", observed_at=at, rx_bps=10e6, tx_bps=2e6))
                db.add(DeviceMetricSample(device_id=r2.id, observed_at=at, cpu_load=80.0))
            db.add(DeviceMetricSample(device_id=r1.id, observed_at=at, cpu_load=20.0))
            db.add(UispMetricSample(device_id=cpe.id, observed_at=at, signal_dbm=-60.0 - (minutes % 10)))
            # MikroTik radio (agent) and ICMP monitor from NSM.
            db.add(DeviceWirelessSample(device_id=r1.id, interface="wlan1", observed_at=at, clients=3, signal_min=-78.0, signal_avg=-66.0, signal_max=-55.0))
            db.add(DevicePingSample(device_id=r1.id, observed_at=at, target="198.51.100.9", sent=3, received=3 if minutes != 30 else 0,
                                    rtt_min=8.0 if minutes != 30 else None, rtt_avg=10.0 if minutes != 30 else None, rtt_max=25.0 if minutes != 30 else None))
        db.add(User(username=f"ci-cg-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
        db.commit()
        ids = {"customer": customer.id, "north": north.id, "south": south.id, "foreign": foreign.id, "r1": r1.id}

        data = cg.build(db, customer, None, "24h", now)
        charts = {c["id"]: c for c in data["charts"]}
        assert set(charts) == {"traffic", "reporting", "cpu", "signal", "latency", "loss"}, set(charts)
        assert min(v for _t, v in charts["signal"]["series"][1]["points"] if v is not None) == -78.0, "MikroTik radios count too"
        assert max(v for _t, v in charts["latency"]["series"][1]["points"] if v is not None) == 25.0
        losses = [v for _t, v in charts["loss"]["series"][0]["points"] if v is not None]
        assert max(losses) == 50.0 and min(losses) == 0.0, losses  # the 30-minute round lost in a 10-minute slot with 2 rounds
        traffic_in = [v for _t, v in charts["traffic"]["series"][0]["points"] if v is not None]
        assert max(traffic_in) == 50e6 and min(traffic_in) == 40e6, "sum of the WAN interfaces only, r2 missing after its outage"
        points = charts["traffic"]["series"][0]["points"]
        assert points[0][1] is None and len(points) >= 144, "slots without data are gaps"
        reporting = [v for _t, v in charts["reporting"]["series"][0]["points"] if v is not None]
        assert max(reporting) == 2 and min(reporting) == 1
        cpu_max = [v for _t, v in charts["cpu"]["series"][1]["points"] if v is not None]
        assert max(cpu_max) == 80.0
        worst = [v for _t, v in charts["signal"]["series"][1]["points"] if v is not None]
        assert min(worst) <= -65.0
        assert data["top"][0]["name"] == f"TEST-CG-R1-{suffix}" and data["top"][0]["rx_bps"] == 40e6

        north_only = cg.build(db, customer, north.id, "24h", now)
        assert max(v for _t, v in {c["id"]: c for c in north_only["charts"]}["traffic"]["series"][0]["points"] if v is not None) == 40e6
        overview = {(r["site"].name if r["site"] else None): r for r in cg.site_overview(db, customer, now)}
        assert overview[f"Nord {suffix}"]["devices"] == 2 and overview[f"Sud {suffix}"]["online"] == 0 and overview[f"Nord {suffix}"]["rx_bps"] == 40e6
        try:
            cg.build(db, customer, None, "1y")
            raise AssertionError("bad range")
        except ValueError:
            pass

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-cg-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/customers/{ids['customer']}/graphs").text
    assert "Grafici del cliente" in page and "Confronto sedi" in page and f"TEST-CG-R1-{suffix}" in page and "40.00 Mbit/s" in page
    assert f'/customers/{ids["customer"]}/graphs?site={ids["north"]}' in page and "rrd_chart.js" in page and "series_chart.js" in page
    site_page = client.get(f"/customers/{ids['customer']}/graphs?site={ids['south']}").text
    assert f"della sede Sud {suffix}" in site_page and "Confronto sedi" not in site_page
    # Availability from the ICMP monitor: r1 lost one round out of 23.
    assert "Disponibilità (ultimi 30 giorni)" in page and "95.65%" in page and "media 95.65%, 1 sotto il 99%" in page
    assert "Nessun apparato con monitoraggio ICMP" in site_page, "the south site has no ICMP samples"
    assert "Grafici del cliente" in client.get(f"/customers/{ids['customer']}/graphs?site={ids['foreign']}").text, "a site of another customer is ignored"
    api = client.get(f"/api/v1/customers/{ids['customer']}/graphs?range=7d&site={ids['north']}")
    assert api.status_code == 200 and {c["id"] for c in api.json()["charts"]} >= {"traffic", "cpu"}
    assert client.get(f"/api/v1/customers/{ids['customer']}/graphs?range=1y").status_code == 400
    assert f'href="/customers/{ids["customer"]}/graphs"' in client.get(f"/customers/{ids['customer']}").text, "Grafici tab"
    assert "Grafici</a>" in client.get(f"/customers/{ids['customer']}/sites").text
    print("Customer graphs smoke passed")


if __name__ == "__main__":
    main()
