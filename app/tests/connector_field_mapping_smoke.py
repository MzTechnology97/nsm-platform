"""Field mappings checked against the operator's UISP and cnMaestro consoles (shapes only, synthetic values)."""
from datetime import datetime, timezone

from app import cnmaestro_connector as cnm
from app import uisp_firmware
from app import vendor_firmware as vf
from app.cnmaestro_onboarding import device_type_for
from app.models import Device
from app.uisp_operations import _event_type


def main():
    # UISP device log: the kind is in tags, with the generic "device" tag first.
    assert _event_type({"tags": ["device", "device-state"]}) == "device-state"
    assert _event_type({"tags": ["device"]}) == "device"
    assert _event_type({"type": "outage", "tags": ["device-state"]}) == "outage"
    assert _event_type({}) == "" and _event_type({"tags": "device"}) == ""

    # UISP firmware: no latest version, but UISP says an upgrade exists → update available, unless the catalog already decided.
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    row = {"identification": {"firmwareVersion": "8.7.15", "platformId": "WA"}, "overview": {"canUpgrade": True}, "firmware": {"compatible": True}}
    device = Device(vendor="ubiquiti", firmware_version="8.7.15", firmware_status="unknown", inventory_data={})
    uisp_firmware.apply(device, uisp_firmware.extract(row), now)
    assert device.firmware_status == "update_available" and device.recommended_firmware_version is None
    assert vf.platform_of(device) == "WA", "platform from UISP when the firmware string has none"
    judged = Device(vendor="ubiquiti", firmware_version="8.7.15", firmware_status="current", inventory_data={"vendor_firmware": {"latest": "8.7.15"}})
    uisp_firmware.apply(judged, uisp_firmware.extract(row), now)
    assert judged.firmware_status == "current", "the catalog verdict is kept"
    quiet = Device(vendor="ubiquiti", firmware_version="8.7.15", firmware_status="unknown", inventory_data={})
    uisp_firmware.apply(quiet, uisp_firmware.extract({**row, "overview": {"canUpgrade": False}}), now)
    assert quiet.firmware_status == "unknown", "no upgrade offered is not proof of the latest version"
    assert vf.platform_of(Device(vendor="ubiquiti", firmware_version="XW.ar934x.v6.3.6.33330.210818.1900", inventory_data={})) == "XW"
    assert vf.platform_of(Device(vendor="ubiquiti", firmware_version="8.7.15", inventory_data={"uisp_firmware": {"platform": "wa; drop"}})) == ""

    # cnMaestro Wi-Fi APs (cnPilot): clients and traffic per band; uptime as time online.
    wifi = {"mac": "02:CB:00:00:00:10", "status": "online", "status_time": 172800, "cpu": 9, "memory": 41, "type": "wifi-enterprise", "mode": "wi-fi",
            "radio": {"24ghz": {"num_clients": 3, "tx_bps": 120000, "rx_bps": 40000}, "5ghz": {"num_clients": 11, "tx_bps": 2400000, "rx_bps": 600000}}}
    assert cnm.metrics(wifi) == {"cpu_percent": 9, "ram_percent": 41, "stations": 14, "dl_throughput_bps": 2520000, "ul_throughput_bps": 640000,
                                 "uptime_seconds": 172800}, cnm.metrics(wifi)
    assert "uptime_seconds" not in cnm.metrics({**wifi, "status": "offline"}), "time offline is not uptime"
    # ePMP keeps its own keys (no double counting).
    epmp = {"status": "online", "connected_sms": 5, "uptime": 3600, "radio": {"dl_throughput": 9000000, "dl_rssi": -60}}
    assert cnm.metrics(epmp)["stations"] == 5 and cnm.metrics(epmp)["uptime_seconds"] == 3600 and cnm.metrics(epmp)["dl_throughput_bps"] == 9000000
    assert device_type_for(wifi) == "wireless_ap" and device_type_for({"type": "epmp", "mode": "sm"}) == "wireless_cpe"
    print("Connector field mapping smoke passed")


if __name__ == "__main__":
    main()
