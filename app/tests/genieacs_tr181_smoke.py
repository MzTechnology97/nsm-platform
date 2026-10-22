"""GenieACS on TR-181 CPEs (structure of a TP-Link VX230v, synthetic values): management IP and MACs on any instance."""
from types import SimpleNamespace

import app.genieacs_connector as acs

SERIAL = "TP-CI201-VX230V"
LAN_MAC = "02:01:00:00:00:40"
WAN_MAC = "02:01:00:00:00:41"


def parameter(value):
    return {"_value": value, "_type": "xsd:string", "_writable": False}


def tr181_row(*, connection_request_url="http://203.0.113.87:7547/tr069"):
    """WAN on IP.Interface.6 over PPP.Interface.2 (PPPoE), LAN on IP.Interface.1, MACs on Ethernet.Interface/Link."""
    return {
        "_id": "8C86DD-VX230v-TP%2DCI201%2DVX230V",
        "_deviceId": {"_Manufacturer": "TP-Link", "_OUI": "8C86DD", "_ProductClass": "VX230v", "_SerialNumber": SERIAL},
        "_lastInform": "2026-10-22T14:51:04.260Z",
        "Device": {
            "DeviceInfo": {"SoftwareVersion": parameter("0.16.0 2.0.0 v606c.0 Build 240530 Rel.14033n"),
                           "HardwareVersion": parameter("VX230v v1.0 00000000")},
            "ManagementServer": {"ConnectionRequestURL": parameter(connection_request_url)},
            "IP": {"Interface": {
                "1": {"IPv4Address": {"1": {"IPAddress": parameter("198.51.100.1")}}},
                "2": {"IPv4Address": {"1": {"IPAddress": parameter("0.0.0.0")}}},
                "6": {"IPv4Address": {"1": {"IPAddress": parameter("203.0.113.87")}}},
            }},
            "Ethernet": {
                "Interface": {"1": {"MACAddress": parameter(LAN_MAC.lower())}, "2": {"MACAddress": parameter("00:00:00:00:00:00")},  # public-data-safety: allow (all-zero MAC that NSM must skip)
                              "5": {"MACAddress": parameter(WAN_MAC)}},
                "Link": {"1": {"MACAddress": parameter(LAN_MAC)}},
            },
        },
    }


def main():
    connection = SimpleNamespace(settings={"online_window_minutes": 1440, "mac_parameter_paths": list(acs.LEGACY_MAC_PARAMETER_PATHS)})
    # Connectors saved with the old defaults (index 1 only) read the current wildcard defaults.
    assert acs._mac_paths(connection) == acs.DEFAULT_MAC_PARAMETER_PATHS
    custom = SimpleNamespace(settings={"mac_parameter_paths": ["Device.Ethernet.Interface.3.MACAddress"]})
    assert acs._mac_paths(custom) == ("Device.Ethernet.Interface.3.MACAddress",), "operator paths are kept"

    candidate = acs.candidate_from_genieacs(tr181_row(), connection, "serial")
    assert candidate["serial_number"] == SERIAL and candidate["model"] == "VX230v" and candidate["firmware_version"].startswith("0.16.0")
    assert candidate["management_ip"] == "203.0.113.87", candidate["management_ip"]
    assert candidate["macs"] == [LAN_MAC, WAN_MAC] and candidate["primary_mac"] == LAN_MAC, candidate["macs"]

    # Without a usable connection-request URL, the first routable IPv4 of any interface (0.0.0.0 and loopback skipped).
    fallback = acs.candidate_from_genieacs(tr181_row(connection_request_url="http://cpe.example.test:7547/"), connection, "serial")
    assert fallback["management_ip"] == "198.51.100.1"

    # Projection fetches wildcard paths through their parent object; MAC queries stay bounded per path.
    projection = acs._projection(connection)
    assert "Device.IP.Interface" in projection and "Device.Ethernet.Interface" in projection and not any("*" in p for p in projection)
    assert "InternetGatewayDevice.DeviceInfo.SoftwareVersion" in projection
    for path in acs.DEFAULT_MAC_PARAMETER_PATHS:
        expanded = acs._expand_instances(path)
        assert 1 <= len(expanded) <= acs.MAC_QUERY_MAX_PATHS and not any("*" in p for p in expanded), path
    assert "Device.Ethernet.Interface.5.MACAddress" in acs._expand_instances("Device.Ethernet.Interface.*.MACAddress")

    # Search by the WAN MAC (not the first one): one request per path, the CPE matched on any of its MACs.
    calls = []
    original = acs._http_get

    def fake_http_get(conn, *, query, projection):
        calls.append(query)
        keys = [next(iter(item)) for item in query["$or"]]
        return [tr181_row()] if "Device.Ethernet.Interface.5.MACAddress" in keys else []

    acs._http_get = fake_http_get
    try:
        found = acs._query_mac(connection, WAN_MAC.lower())
    finally:
        acs._http_get = original
    assert len(calls) == len(acs.DEFAULT_MAC_PARAMETER_PATHS) and all(len(q["$or"]) <= acs.MAC_QUERY_MAX_PATHS for q in calls)
    assert len(found) == 1 and found[0]["matched_by"] == "mac"

    # A record whose MAC is the WAN one matches the CPE even if NSM's first MAC is the LAN one.
    device = SimpleNamespace(primary_mac=WAN_MAC, serial_number=SERIAL)
    assert acs._mac_matches(device, candidate)
    assert not acs._mac_matches(SimpleNamespace(primary_mac="02:01:00:00:00:99"), candidate)

    # The base provision asks every CPE for its interface MACs.
    from app import acs_admin
    assert "Device.Ethernet.Interface.*.MACAddress" in acs_admin.BASE_PROVISION_SCRIPT
    print("GenieACS TR-181 smoke passed")


if __name__ == "__main__":
    main()
