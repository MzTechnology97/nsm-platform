"""ACS-05: state, samples and charts of TR-069 CPEs read from GenieACS; current resources and online state on the device."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_monitoring as mon
from app import genieacs_connector as acs
from app.acs_models import AcsMetricSample
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-History-2026"


def v(value):
    return {"_value": value}


def tr181(inform, rx, tx, uptime=86400, clients=3):
    return {
        "_lastInform": inform.isoformat(), "_lastBoot": (inform - timedelta(seconds=uptime)).isoformat(),
        "Device": {
            "DeviceInfo": {"UpTime": v(uptime), "ProcessStatus": {"CPUUsage": v(12)}, "MemoryStatus": {"Total": v(200000), "Free": v(50000)}},
            "ManagementServer": {"PeriodicInformInterval": v(300), "ConnectionRequestURL": v("http://203.0.113.80:7547/cr")},
            "PPP": {"Interface": {"1": {"ConnectionStatus": v("Connected"), "LastConnectionError": v("ERROR_NONE")}}},
            "IP": {"Interface": {
                "1": {"Status": v("Up"), "LowerLayers": v("Device.Bridging.Bridge.1.Port.1"), "IPv4Address": {"1": {"IPAddress": v("192.0.2.1")}}},
                "2": {"Status": v("Up"), "LowerLayers": v("Device.PPP.Interface.1"), "LastChange": v(3600),
                      "IPv4Address": {"1": {"IPAddress": v("203.0.113.80")}}, "Stats": {"BytesReceived": v(rx), "BytesSent": v(tx)}},
            }},
            "WiFi": {"Radio": {"1": {"OperatingFrequencyBand": v("2.4GHz"), "Channel": v(6), "Status": v("Up")}},
                     "SSID": {"1": {"LowerLayers": v("Device.WiFi.Radio.1")}},
                     "AccessPoint": {"1": {"AssociatedDeviceNumberOfEntries": v(clients), "SSIDReference": v("Device.WiFi.SSID.1")}}},
            "Hosts": {"HostNumberOfEntries": v(5)},
        },
    }


def main():
    suffix = uuid.uuid4().hex[:6].upper()
    t0 = utcnow().replace(microsecond=0) - timedelta(minutes=20)
    ext181, ext098 = f"202BC1-VX230v-H{suffix}", f"00259E-HG8245-H{suffix}"
    records = {
        ext181: {"_id": ext181, **tr181(t0, rx=1_000_000, tx=200_000)},
        ext098: {"_id": ext098, "_lastInform": t0.isoformat(), "InternetGatewayDevice": {
            "DeviceInfo": {"UpTime": v(7200)}, "ManagementServer": {"PeriodicInformInterval": v(600)},
            "WANDevice": {"1": {"WANCommonInterfaceConfig": {"TotalBytesReceived": v(5000), "TotalBytesSent": v(1000)},
                                "WANConnectionDevice": {"1": {"WANPPPConnection": {"1": {"ConnectionStatus": v("Connected"), "ExternalIPAddress": v("198.51.100.90"),
                                                                                         "Uptime": v(1800)}}}}}},
            "LANDevice": {"1": {"Hosts": {"HostNumberOfEntries": v(2)}, "WLANConfiguration": {"1": {"TotalAssociations": v(1), "Channel": v(36)}}}}}},
    }

    def fake_get(connection, *, query, projection):
        assert "Device.DeviceInfo.UpTime" in projection
        return [dict(records[i]) for i in query["_id"]["$in"] if i in records]

    original = acs._http_get
    acs._http_get = fake_get
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text('{"mode": "none"}'), is_enabled=True, settings={}))
        customer = Customer(name=f"CI ACS Hist {suffix}", code=f"AH{suffix}")
        db.add(customer)
        db.flush()
        a = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSH-VX-{suffix}", external_device_id=ext181,
                   management_source="tr069", status="unknown")
        b = Device(customer_id=customer.id, vendor="generic", device_type="ont", name=f"TEST-ACSH-HG-{suffix}", external_device_id=ext098,
                   management_source="tr069", status="unknown")
        db.add_all([a, b])
        db.add(User(username=f"ci-acsh-{suffix}", password_hash=hash_password(PASSWORD),
                    role="operator", is_active=True))
        db.commit()
        ids = {"a": a.id, "b": b.id}

    # First Inform: state and one sample, no rate yet (one counter reading).
    stats = mon.collect(now=t0 + timedelta(seconds=30), force=True)
    assert stats["status"] == "success" and stats["devices"] == 2 and stats["samples"] == 2 and stats["online"] == 2, stats
    # Second Inform 300 s later: WAN rates from the byte counters.
    records[ext181].update(tr181(t0 + timedelta(seconds=300), rx=1_000_000 + 3_750_000, tx=200_000 + 375_000, clients=4))
    assert mon.collect(now=t0 + timedelta(seconds=330), force=True)["samples"] == 1, "same Inform twice for the TR-098 CPE: no new sample"
    with SessionLocal() as db:
        samples = list(db.scalars(select(AcsMetricSample).where(AcsMetricSample.device_id == ids["a"]).order_by(AcsMetricSample.observed_at)))
        assert len(samples) == 2 and samples[0].wan_rx_bps is None
        assert samples[1].wan_rx_bps == 100_000 and samples[1].wan_tx_bps == 10_000 and samples[1].wifi_clients == 4 and samples[1].cpu_percent == 12
        state = db.get(Device, ids["a"]).inventory_data["tr069"]["monitor"]
        assert state["wan"]["type"] == "PPPoE" and state["wan"]["ip"] == "203.0.113.80" and state["ram"] == 75.0 and state["lan_hosts"] == 5
        b_state = db.get(Device, ids["b"]).inventory_data["tr069"]["monitor"]
        assert b_state["data_model"] == "TR-098" and b_state["wan"]["ip"] == "198.51.100.90" and b_state["wifi_clients"] == 1

    # Counters stuck at zero with the WAN up (VX230v firmware): "not reported", no traffic samples.
    records[ext181].update(tr181(t0 + timedelta(seconds=600), rx=0, tx=0))
    mon.collect(now=t0 + timedelta(seconds=630), force=True)
    with SessionLocal() as db:
        state = db.get(Device, ids["a"]).inventory_data["tr069"]["monitor"]
        assert state["wan"]["counters_reported"] is False
        last = db.scalar(select(AcsMetricSample).where(AcsMetricSample.device_id == ids["a"]).order_by(AcsMetricSample.observed_at.desc()))
        assert last.wan_rx_bps is None and last.cpu_percent == 12

    # Device page: current resources from the monitor, the ACS tab state and charts.
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-acsh-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
    detail = client.get(f"/api/ui/v1/devices/{ids['a']}").json()["device"]
    assert detail["telemetry"] == {"cpu": 12.0, "ram": 75.0, "uptime_seconds": 86400} and detail["status"] == "online", detail["telemetry"]
    monitor = client.get(f"/api/ui/v1/devices/{ids['a']}/acs/monitor?range=24h").json()
    assert monitor["state"]["wan"]["status"] == "Connected" and monitor["sample_count"] == 3, monitor
    assert {c["id"] for c in monitor["charts"]} >= {"acs_wan", "resources", "acs_clients"}
    assert client.get(f"/api/ui/v1/devices/{ids['a']}/acs/monitor?range=1y").status_code == 400

    # Three missed periodic Informs: offline; values older than 2 hours are no longer "current".
    mon.collect(now=t0 + timedelta(seconds=600) + timedelta(minutes=16), force=True)
    with SessionLocal() as db:
        assert db.get(Device, ids["a"]).status == "offline"
    later = mon.CURRENT_MAX_AGE
    with SessionLocal() as db:
        device = db.get(Device, ids["a"])
        data = dict(device.inventory_data)
        data["tr069"] = {**data["tr069"], "monitor": {**data["tr069"]["monitor"], "last_inform": (utcnow() - later - timedelta(minutes=1)).isoformat()}}
        device.inventory_data = data
        db.commit()
        assert mon.current(db.get(Device, ids["a"])) is None

    # Retention keeps the newest sample of each device.
    with SessionLocal() as db:
        for sample in db.scalars(select(AcsMetricSample).where(AcsMetricSample.device_id.in_(list(ids.values())))):
            sample.observed_at -= timedelta(days=mon.RETENTION_DAYS + 1)
        db.commit()
    assert mon.cleanup() >= 2
    with SessionLocal() as db:
        assert db.scalar(select(AcsMetricSample).where(AcsMetricSample.device_id == ids["a"])) is not None

    acs._http_get = original
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.commit()
    print("ACS history smoke passed")


if __name__ == "__main__":
    main()
