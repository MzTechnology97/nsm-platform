"""ACS-05: PON optical levels of TR-069 CPEs (TR-181 Device.Optical, TR-098 vendor objects): units, samples, charts,
alert under threshold, hourly provision read, and MON-02 retention that keeps the last sample of every CPE."""
import json
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import acs_admin, acs_alerts, acs_pon
from app import acs_monitoring as mon
from app import genieacs_connector as acs
from app.acs_models import AcsMetricSample
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, Customer, Device, User, utcnow
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-Pon-2026"


def v(value, stamp="2026-10-23T10:00:00.000Z"):
    return {"_value": value, "_timestamp": stamp, "_writable": False}


def tr181(inform, rx_level):
    return {"_lastInform": inform.isoformat(), "Device": {
        "DeviceInfo": {"UpTime": v(86400)}, "ManagementServer": {"PeriodicInformInterval": v(300)},
        "Optical": {"Interface": {"1": {"Status": v("Up"), "OpticalSignalLevel": v(rx_level), "TransmitOpticalLevel": v(2300),
                                        "X_EXAMPLE_Temperature": v(45), "X_EXAMPLE_Voltage": v(3300), "X_EXAMPLE_BiasCurrent": v(12.5)}}}}}


def units():
    # TR-181: 0.001 dBm integers; vendor temperature °C, voltage mV, bias mA.
    pon = acs_pon.parse(tr181(utcnow(), -19500))
    assert pon["rx_dbm"] == -19.5 and pon["tx_dbm"] == 2.3 and pon["temperature_c"] == 45 and pon["voltage_v"] == 3.3 and pon["bias_ma"] == 12.5, pon
    assert pon["status"] == "Up" and pon["source"] == "Device.Optical.Interface.1" and pon["read_at"].startswith("2026-10-23"), pon
    # Huawei (TR-098, the object name is spelled X_GponInterafceConfig): dBm decimals, °C, V, mA.
    huawei = {"InternetGatewayDevice": {"WANDevice": {"1": {"X_GponInterafceConfig": {
        "RXPower": v("-21.37"), "TXPower": v("2.15"), "TransceiverTemperature": v("48"), "SupplyVoltage": v("3.28"), "BiasCurrent": v("11")}}}}}
    pon = acs_pon.parse(huawei)
    assert pon == {**pon, "rx_dbm": -21.37, "tx_dbm": 2.15, "temperature_c": 48, "voltage_v": 3.28, "bias_ma": 11}, pon
    # CT-COM style: power in 0.1 µW, temperature in 1/256 °C, voltage in 100 µV, bias in 2 µA.
    ctcom = {"InternetGatewayDevice": {"WANDevice": {"1": {"X_CT-COM_GponInterfaceConfig": {
        "RXPower": v("100"), "TXPower": v("20000"), "TransceiverTemperature": v("11520"), "SupplyVottage": v("33000"), "BiasCurrent": v("6000")}}}}}
    pon = acs_pon.parse(ctcom)
    assert pon["rx_dbm"] == -20.0 and pon["tx_dbm"] == 3.01 and pon["temperature_c"] == 45 and pon["voltage_v"] == 3.3 and pon["bias_ma"] == 12, pon
    # ZTE: generic WAN PON object.
    zte = {"InternetGatewayDevice": {"WANDevice": {"1": {"X_ZTE-COM_WANPONInterfaceConfig": {"RXPower": v("-24.1"), "TXPower": v("1.9")}}}}}
    assert acs_pon.parse(zte)["rx_dbm"] == -24.1
    # Implausible or missing values stay empty; CPEs without optics (DSL VX230v, Ethernet HB210) give None.
    weird = {"Device": {"Optical": {"Interface": {"1": {"OpticalSignalLevel": v("abc"), "X_EXAMPLE_Temperature": v(99999)}}}}}
    assert acs_pon.parse(weird) is None
    assert acs_pon.parse({"Device": {"DSL": {"Line": {"1": {"Status": v("Up")}}}}}) is None
    assert acs_pon.parse({"InternetGatewayDevice": {"WANDevice": {"1": {"WANDSLInterfaceConfig": {"Status": v("Up")}}}}}) is None
    # "Response" in a name is not a PON object.
    assert acs_pon.parse({"InternetGatewayDevice": {"WANDevice": {"1": {"X_ResponseConfig": {"RXPower": v("-20")}}}}}) is None


def provision():
    script = acs_admin.BASE_PROVISION_SCRIPT
    for path in acs_pon.PROVISION_PATHS:
        assert f'"{path}"' in script, path
    # Read only, once an hour: no refreshObject, no write of these values.
    block = script[script.index("// PON optical levels"):script.index("// Interface MAC addresses")]
    assert "declare(path, {value: hourly});" in block and "refreshObject" not in script and "{value: hourly}, {" not in block


def main():
    units()
    provision()
    suffix = uuid.uuid4().hex[:6].upper()
    t0 = utcnow().replace(microsecond=0) - timedelta(hours=3)
    ext = f"02AB01-ONT-P{suffix}"
    records = {ext: {"_id": ext, **tr181(t0, -19500)}}

    def fake_get(connection, *, query, projection):
        assert "Device.Optical" in projection
        return [dict(records[i]) for i in query["_id"]["$in"] if i in records]

    original = acs._http_get
    acs._http_get = fake_get
    with SessionLocal() as db:
        db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
        db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                    secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
        customer = Customer(name=f"CI ACS Pon {suffix}", code=f"AP{suffix}")
        db.add(customer)
        db.flush()
        ont = Device(customer_id=customer.id, vendor="generic", device_type="ont", name=f"TEST-ACSP-ONT-{suffix}", external_device_id=ext,
                     management_source="tr069", status="unknown")
        db.add(ont)
        db.add(User(username=f"ci-acsp-{suffix}", password_hash=hash_password(PASSWORD), role="operator", is_active=True))
        db.commit()
        ont_id = ont.id

    try:
        assert mon.collect(now=t0 + timedelta(seconds=30), force=True)["samples"] == 1
        with SessionLocal() as db:
            sample = db.scalar(select(AcsMetricSample).where(AcsMetricSample.device_id == ont_id))
            assert sample.pon_rx_dbm == -19.5 and sample.pon_tx_dbm == 2.3 and sample.pon_temperature_c == 45 and sample.pon_bias_ma == 12.5
            assert db.get(Device, ont_id).inventory_data["tr069"]["monitor"]["pon"]["rx_dbm"] == -19.5

        # Rx falls under the threshold (-27 dBm): one alert after two low readings, closed when the level recovers.
        for step, level in ((1, -28800), (2, -29100)):
            at = t0 + timedelta(minutes=5 * step)
            records[ext] = {"_id": ext, **tr181(at, level)}
            mon.collect(now=at + timedelta(seconds=30), force=True)
            acs_alerts.evaluate(now=at + timedelta(seconds=40), force=True)
            with SessionLocal() as db:
                kinds = [(i.details or {}).get("kind") for i in db.scalars(select(ActionIssue).where(
                    ActionIssue.device_id == ont_id, ActionIssue.category == "acs", ActionIssue.status == "open"))]
            assert kinds == ([] if step == 1 else ["pon"]), (step, kinds)
        with SessionLocal() as db:
            issue = db.scalar(select(ActionIssue).where(ActionIssue.device_id == ont_id, ActionIssue.category == "acs"))
            assert issue.details["rx_dbm"] == -29.1 and issue.details["threshold_dbm"] == -27.0, issue.details
        at = t0 + timedelta(minutes=15)
        records[ext] = {"_id": ext, **tr181(at, -20000)}
        mon.collect(now=at + timedelta(seconds=30), force=True)
        acs_alerts.evaluate(now=at + timedelta(seconds=40), force=True)
        with SessionLocal() as db:
            assert db.scalar(select(ActionIssue).where(ActionIssue.device_id == ont_id, ActionIssue.category == "acs")).status == "resolved"

        # The ACS tab: state with the optics and the PON charts.
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": f"ci-acsp-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        monitor = client.get(f"/api/ui/v1/devices/{ont_id}/acs/monitor?range=24h").json()
        assert monitor["state"]["pon"]["rx_dbm"] == -20.0 and monitor["sample_count"] == 4, monitor["state"]
        assert {"acs_pon_power", "acs_pon_temperature", "acs_pon_bias", "acs_pon_voltage"} <= {c["id"] for c in monitor["charts"]}
        assert monitor["last_sample_at"]

        # MON-02: optics no longer reported by the last samples; everything is past the retention.
        with SessionLocal() as db:
            for minutes in (20, 25):
                db.add(AcsMetricSample(device_id=ont_id, observed_at=t0 + timedelta(minutes=minutes), cpu_percent=5))
            db.flush()
            for sample in db.scalars(select(AcsMetricSample).where(AcsMetricSample.device_id == ont_id)):
                sample.observed_at -= timedelta(days=mon.RETENTION_DAYS + 5)
            db.commit()
        assert mon.cleanup() >= 4
        with SessionLocal() as db:
            kept = list(db.scalars(select(AcsMetricSample).where(AcsMetricSample.device_id == ont_id).order_by(AcsMetricSample.observed_at)))
            # The newest sample of the CPE and its newest optical reading survive.
            assert len(kept) == 2 and kept[0].pon_rx_dbm == -20.0 and kept[1].pon_rx_dbm is None and kept[1].cpu_percent == 5, \
                [(k.observed_at, k.pon_rx_dbm) for k in kept]
        # The tab still says when NSM last heard from the CPE.
        monitor = client.get(f"/api/ui/v1/devices/{ont_id}/acs/monitor?range=24h").json()
        assert monitor["sample_count"] == 0 and monitor["last_sample_at"] and monitor["state"]["pon"]["rx_dbm"] == -20.0

        # Settings: threshold validated, 0 disables it.
        with SessionLocal() as db:
            db.get(User, db.scalar(select(User.id).where(User.username == f"ci-acsp-{suffix}"))).role = "admin"
            db.commit()
        client.headers["X-CSRF-Token"] = client.get("/api/ui/v1/session").json()["csrf_token"]
        current = client.get("/api/ui/v1/admin/acs/alerts").json()
        assert current["pon_rx_min_dbm"] == -27.0
        assert client.post("/api/ui/v1/admin/acs/alerts", json={**current, "pon_rx_min_dbm": -60}).status_code == 422
        assert client.post("/api/ui/v1/admin/acs/alerts", json={**current, "pon_rx_min_dbm": 0}).json()["pon_rx_min_dbm"] == 0
    finally:
        acs._http_get = original
        with SessionLocal() as db:
            db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
            db.commit()
    print("ACS PON optics smoke passed")


if __name__ == "__main__":
    main()
