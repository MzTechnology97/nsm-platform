"""ACS-04: one map of the TR-098 / TR-181 paths, explicit unsupported fields, and the capability view of a CPE
(read-only: one GenieACS GET, no task)."""
import json
import re
import uuid

import httpx
from fastapi.testclient import TestClient

from app import acs_diagnostics as nbi
from app import acs_actions, acs_firmware, acs_profiles, acs_snapshot, acs_speedtest, acs_syslog, tplink_cloud_firmware
from app.db import SessionLocal
from app.entrypoint import app
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, User
from app.secret_vault import encrypt_text
from app.security import hash_password

PASSWORD = "CI-ACS-Profiles-2026"


def v(value, writable=False):
    return {"_value": value, "_writable": writable, "_timestamp": "2026-10-23T10:00:00.000Z"}


def tplink_tr181(ext):
    """Sanitized shape of a TP-Link VX230v (DSL, Aginet TR-181)."""
    return {"_id": ext, "_deviceId": {"_Manufacturer": "TP-Link", "_ProductClass": "VX230v", "_OUI": "202BC1"}, "_lastInform": "2026-10-23T10:00:00.000Z",
            "Device": {
                "DeviceInfo": {"SoftwareVersion": v("0.19.0"), "ProcessStatus": {"CPUUsage": v(5)}, "MemoryStatus": {"Total": v(1000)},
                               "X_TP_LogCfg": {"ServerIP": v("", True)}},
                "ManagementServer": {"PeriodicInformInterval": v(300, True), "URL": v("http://acs.example.test:7547/", True)},
                "PPP": {"InterfaceNumberOfEntries": v(1), "Interface": {"1": {"ConnectionStatus": v("Connected")}}},
                "IP": {"InterfaceNumberOfEntries": v(3), "Interface": {"1": {"Status": v("Up")}}, "Diagnostics": {
                    "IPPing": {"DiagnosticsState": v("None", True)}, "TraceRoute": {"DiagnosticsState": v("None", True)},
                    "DownloadDiagnostics": {"DiagnosticsState": v("None", True)}, "UploadDiagnostics": {"DiagnosticsState": v("None", True)}}},
                "WiFi": {"RadioNumberOfEntries": v(2), "SSIDNumberOfEntries": v(4), "Radio": {"1": {"Channel": v(6, True)}}},
                "DSL": {"Line": {"1": {"Status": v("Up")}}},
                "NAT": {"PortMappingNumberOfEntries": v(0), "X_TP_UPnPCfg": {"Enable": v(True, True)}},
                "UserInterface": {"RemoteAccess": {"Enable": v(False, True)}},
                "X_TP_CloudService": {"FwUpgradeInfo": {"Version": v("")}},
            }}


def huawei_tr098(ext):
    """Sanitized shape of a TR-098 GPON ONT (Huawei object names)."""
    conn = {"ConnectionStatus": v("Connected"), "PortMappingNumberOfEntries": v(0), "PortMapping": {}}
    return {"_id": ext, "_deviceId": {"_Manufacturer": "Huawei", "_ProductClass": "HG8245", "_OUI": "00259E"}, "_lastInform": "2026-10-23T10:00:00.000Z",
            "InternetGatewayDevice": {
                "DeviceInfo": {"SoftwareVersion": v("V3R017")},
                "ManagementServer": {"PeriodicInformInterval": v(600, True), "URL": v("http://acs.example.test:7547/", True)},
                "WANDevice": {"1": {"WANConnectionDevice": {"1": {"WANPPPConnection": {"1": conn}}},
                                    "X_GponInterafceConfig": {"RXPower": v("-20.1"), "TXPower": v("2.0")}}},
                "LANDevice": {"1": {"WLANConfiguration": {"1": {"SSID": v("example", True)}}}},
                "IPPingDiagnostics": {"DiagnosticsState": v("None", True)},
                "TraceRouteDiagnostics": {"DiagnosticsState": v("None", True)},
            }}


def by_id(result):
    return {c["id"]: c for c in result["capabilities"]}


def main():
    # One map: the ACS modules take their object paths from acs_profiles.
    assert acs_speedtest.OBJECTS["tr181"]["download"] == acs_profiles.path("tr181", "download")
    assert acs_speedtest.ACS_URL["tr098"] == acs_profiles.path("tr098", "acs_url")
    assert nbi.MODELS["tr098"]["ping"][0] == acs_profiles.path("tr098", "ping")
    assert acs_actions.TR098_WLAN == acs_profiles.path("tr098", "wifi") and acs_actions.TR181_WIFI == acs_profiles.path("tr181", "wifi")
    assert acs_syslog.ROOT == acs_profiles.vendor_path("tp-link", "syslog")
    assert tplink_cloud_firmware.CLOUD == acs_profiles.vendor_path("tp-link", "tplink_cloud")
    assert acs_firmware.ROOTS == acs_profiles.ROOT and set(acs_snapshot.ROOTS) == set(acs_profiles.ROOT.values())
    # Probes are leaves or small objects: never a whole root, never a wildcard.
    assert not set(acs_profiles.PROBES) & set(acs_profiles.ROOT.values()) and not any("*" in p for p in acs_profiles.PROBES)

    tp = by_id(acs_profiles.capabilities(tplink_tr181("x"), snapshot_count=2))
    assert all(tp[c]["status"] == "available" for c in ("identity", "resources", "wan", "wifi", "dsl", "ping", "traceroute", "speedtest",
                                                         "firmware_cloud", "syslog", "port_mapping", "upnp", "remote_access", "inform_interval",
                                                         "config_snapshot")), {k: c["status"] for k, c in tp.items()}
    assert tp["pon"]["status"] == "not_exposed" and tp["pon"]["reason"] == "no_pon"
    assert tp["firmware"]["status"] == "rpc" and tp["reboot"]["status"] == "rpc"
    assert tp["config_file"] == {**tp["config_file"], "status": "unsupported", "reason": "no_upload_task"}
    assert tp["wifi"]["writes"] and not tp["pon"]["writes"] and tp["syslog"]["paths"] == ["Device.DeviceInfo.X_TP_LogCfg.ServerIP"]

    hw_result = acs_profiles.capabilities(huawei_tr098("y"))
    hw = by_id(hw_result)
    assert hw_result["data_model"] == "tr098" and hw_result["vendor_profile"] is None
    assert hw["pon"]["status"] == "available" and hw["pon"]["paths"] == ["InternetGatewayDevice.WANDevice.1.X_GponInterafceConfig"]
    assert hw["port_mapping"]["status"] == "available" and hw["wan"]["status"] == "available" and hw["dsl"]["status"] == "not_exposed"
    # Explicit unsupported fields, with the reason.
    assert {k: hw[k]["reason"] for k in ("resources", "syslog", "firmware_cloud", "upnp", "config_file")} == {
        "resources": "nsm_tr098", "syslog": "vendor_tr181", "firmware_cloud": "vendor_tr181", "upnp": "no_standard", "config_file": "no_upload_task"}
    assert hw["speedtest"]["status"] == "not_exposed" and hw["config_snapshot"]["reason"] == "no_snapshot"
    # Another vendor on TR-181: TP-Link extensions are not offered.
    other = by_id(acs_profiles.capabilities({**tplink_tr181("z"), "_deviceId": {"_Manufacturer": "Example"}}))
    assert other["syslog"]["reason"] == "vendor_tplink" and other["firmware_cloud"]["status"] == "unsupported"
    assert acs_profiles.capabilities({"_id": "n"})["reason"] == "not_read"

    # Route: one GET to GenieACS with the probes, nothing else.
    suffix = uuid.uuid4().hex[:6].upper()
    ext = f"202BC1-VX230v-P{suffix}"
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path, request.url.params.get("projection")))
        if request.method == "GET":
            return httpx.Response(200, json=[tplink_tr181(ext)])
        return httpx.Response(500)

    nbi.TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as db:
            db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
            db.add(ConnectorIntegration(provider="genieacs", name="GenieACS", base_url="http://genieacs.example.test:7557",
                                        secret_encrypted=encrypt_text(json.dumps({"mode": "none"})), is_enabled=True, settings={}))
            customer = Customer(name=f"CI ACS Prof {suffix}", code=f"PF{suffix}")
            db.add(customer)
            db.flush()
            linked = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSPF-{suffix}", external_device_id=ext,
                            management_source="tr069", status="online")
            unlinked = Device(customer_id=customer.id, vendor="tp-link", device_type="router", name=f"TEST-ACSPF-U-{suffix}", management_source="tr069")
            db.add_all([linked, unlinked])
            db.add(User(username=f"ci-acspf-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
            db.commit()
            ids = (linked.id, unlinked.id)
        client = TestClient(app)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": f"ci-acspf-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303
        body = client.get(f"/api/ui/v1/devices/{ids[0]}/acs/capabilities").json()
        assert body["data_model"] == "tr181" and body["vendor_profile"] == "tp-link" and body["product_class"] == "VX230v", body
        assert by_id(body)["syslog"]["status"] == "available"
        assert [c[0] for c in calls] == ["GET"] and "Device.Optical" in calls[0][2] and ",Device," not in f",{calls[0][2]},"
        assert client.get(f"/api/ui/v1/devices/{ids[1]}/acs/capabilities").status_code == 409
    finally:
        nbi.TRANSPORT = None
        with SessionLocal() as db:
            db.query(ConnectorIntegration).filter_by(provider="genieacs").delete()
            db.commit()
    print("ACS parameter profiles smoke passed")


if __name__ == "__main__":
    main()
