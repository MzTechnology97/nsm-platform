"""ACS monitoring (ACS-05): state and metrics of TR-069 CPEs read from GenieACS.

- The NSM base provision refreshes the monitored parameters at every Inform (every 5 minutes).
- The worker reads them for every associated CPE (one NBI query per batch), keeps the latest state on
  the device (``inventory_data["tr069"]["monitor"]``) and stores one sample per new Inform (90 days).
- Online/offline comes from the last Inform: offline after three missed periodic Informs (at least 15 minutes).
- TR-181 (``Device.*``) and TR-098 (``InternetGatewayDevice.*``) are both read; a value the CPE does not
  report stays empty. WAN byte counters stuck at zero while the WAN is up are treated as not reported
  (the TP-Link VX230v firmware leaves them at 0).
"""
from __future__ import annotations

import ipaddress
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from app import acs_port_mapping
from app import genieacs_connector as acs
from app.acs_models import AcsMetricSample
from app.db import SessionLocal
from app.models import Device, utcnow
from app.telemetry_retention import expire_keep_latest
from app.ui_api import ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
# Values older than this are not shown as the device's current resources.
CURRENT_MAX_AGE = timedelta(hours=2)

RETENTION_DAYS = 90
RUN_INTERVAL_SECONDS = 120
BATCH = 20
DEFAULT_INFORM_SECONDS = 300
MIN_OFFLINE_AFTER = timedelta(minutes=15)
# Counters that stay at zero this long after boot, with the WAN up, are "not reported" rather than "no traffic".
ZERO_COUNTER_GRACE_SECONDS = 600
RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90)}
CHARTS = (
    ("acs_wan", "Traffico WAN", "bps", (("wan_rx_bps", "Download"), ("wan_tx_bps", "Upload"))),
    ("resources", "CPU e memoria", "%", (("cpu_percent", "CPU"), ("ram_percent", "Memoria"))),
    ("acs_clients", "Client Wi-Fi e host LAN", "", (("wifi_clients", "Client Wi-Fi"), ("lan_hosts", "Host LAN"))),
    ("acs_dsl_rate", "Velocità linea DSL", "bps", (("dsl_down_bps", "Downstream"), ("dsl_up_bps", "Upstream"))),
    ("acs_dsl_quality", "Qualità linea DSL", "dB", (("dsl_snr_down_db", "Margine SNR"), ("dsl_attenuation_down_db", "Attenuazione"))),
)
PROJECTION = (
    "_id", "_lastInform", "_lastBoot",
    "Device.DeviceInfo.UpTime", "Device.DeviceInfo.ProcessStatus.CPUUsage", "Device.DeviceInfo.MemoryStatus",
    "Device.ManagementServer.PeriodicInformInterval", "Device.ManagementServer.ConnectionRequestURL",
    "Device.PPP.Interface", "Device.IP.Interface", "Device.WiFi.Radio", "Device.WiFi.SSID", "Device.WiFi.AccessPoint",
    "Device.Hosts.HostNumberOfEntries", "Device.DSL.Line", "Device.DSL.Channel",
    "InternetGatewayDevice.DeviceInfo.UpTime", "InternetGatewayDevice.ManagementServer.PeriodicInformInterval",
    "InternetGatewayDevice.ManagementServer.ConnectionRequestURL", "InternetGatewayDevice.WANDevice", "InternetGatewayDevice.LANDevice",
    *acs_port_mapping.PROJECTION,
)

_last_run = 0.0


# --- Reading values ------------------------------------------------------------------------------

def _value(node, *parts):
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, dict):
        return node.get("_value") if "_value" in node else None
    return node


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _instances(node) -> list[tuple[str, dict]]:
    if not isinstance(node, dict):
        return []
    keys = sorted((key for key in node if key.isdigit() and isinstance(node[key], dict)), key=int)
    return [(key, node[key]) for key in keys]


def _ipv4(value) -> str | None:
    try:
        address = ipaddress.IPv4Address(str(value or "").strip())
    except ValueError:
        return None
    return None if address.is_unspecified or address.is_loopback or address.is_link_local else str(address)


def _is_public(address: str | None) -> bool:
    return bool(address) and ipaddress.IPv4Address(address).is_global


def _up(status) -> bool:
    return str(status or "").strip().lower() in {"up", "connected", "enabled"}


def _tenths(value):
    """TR-181/TR-098 noise margin and attenuation are in 0.1 dB."""
    number = _float(value)
    return round(number / 10, 1) if number is not None else None


# --- TR-181 --------------------------------------------------------------------------------------

def _tr181(root: dict, cr_host: str | None) -> dict:
    info = root.get("DeviceInfo") or {}
    total = _float(_value(info, "MemoryStatus", "Total"))
    free = _float(_value(info, "MemoryStatus", "Free"))
    state = {
        "data_model": "TR-181",
        "uptime": _int(_value(info, "UpTime")),
        "cpu": _float(_value(info, "ProcessStatus", "CPUUsage")),
        "ram": round((total - free) / total * 100, 1) if total and free is not None and total > 0 else None,
        "lan_hosts": _int(_value(root, "Hosts", "HostNumberOfEntries")),
    }
    # WAN: the IP interface that holds the connection-request address, else the first PPP one up, else the first public one.
    interfaces = []
    for index, iface in _instances(_value_obj(root, "IP", "Interface")):
        addresses = [_ipv4(_value(addr, "IPAddress")) for _i, addr in _instances(iface.get("IPv4Address"))]
        addresses = [a for a in addresses if a]
        interfaces.append({"index": index, "node": iface, "addresses": addresses, "status": _value(iface, "Status"),
                           "lower": str(_value(iface, "LowerLayers") or "")})
    wan = (next((i for i in interfaces if cr_host and cr_host in i["addresses"]), None)
           or next((i for i in interfaces if _up(i["status"]) and i["lower"].startswith("Device.PPP.Interface.")), None)
           or next((i for i in interfaces if _up(i["status"]) and any(_is_public(a) for a in i["addresses"])), None))
    if wan:
        node = wan["node"]
        ppp = None
        if wan["lower"].startswith("Device.PPP.Interface."):
            ppp_index = wan["lower"].split(".")[3] if len(wan["lower"].split(".")) > 3 else ""
            ppp = _value_obj(root, "PPP", "Interface", ppp_index)
        last_change = _int(_value(node, "LastChange"))
        connected_for = last_change if last_change else _int(_value(node, "X_TP_Uptime"))
        state["wan"] = {
            "status": (_value(ppp, "ConnectionStatus") if ppp else None) or _value(node, "Status"),
            "type": "PPPoE" if ppp else (_value(node, "IPv4Address", "1", "AddressingType") or "IP"),
            "ip": next((a for a in wan["addresses"] if a == cr_host), wan["addresses"][0] if wan["addresses"] else None),
            "connected_for": connected_for or None,
            "last_error": (_value(ppp, "LastConnectionError") if ppp else None),
            "up": _up(_value(ppp, "ConnectionStatus") if ppp else _value(node, "Status")),
            # PPPoE username the CPE logs in with: matched to the ISPBilling RADIUS account (ispbilling_radius).
            "username": (str(_value(ppp, "Username") or "").strip() or None) if ppp else None,
        }
        state["counters"] = {"rx": _int(_value(node, "Stats", "BytesReceived")), "tx": _int(_value(node, "Stats", "BytesSent"))}
    # Wi-Fi: clients per radio through AccessPoint → SSID → Radio.
    wifi = _value_obj(root, "WiFi") or {}
    ssid_radio = {key: str(_value(node, "LowerLayers") or "") for key, node in _instances(wifi.get("SSID"))}
    clients_by_radio: dict[str, int] = {}
    total_clients = None
    for _key, ap in _instances(wifi.get("AccessPoint")):
        count = _int(_value(ap, "AssociatedDeviceNumberOfEntries"))
        if count is None:
            continue
        total_clients = (total_clients or 0) + count
        ssid = str(_value(ap, "SSIDReference") or "").rstrip(".").split(".")[-1]
        radio = ssid_radio.get(ssid, "").rstrip(".").split(".")[-1]
        clients_by_radio[radio] = clients_by_radio.get(radio, 0) + count
    state["wifi"] = [{
        "band": _value(radio, "OperatingFrequencyBand"), "enabled": _value(radio, "Enable"), "status": _value(radio, "Status"),
        "channel": _int(_value(radio, "Channel")), "bandwidth": _value(radio, "CurrentOperatingChannelBandwidth"),
        "standards": _value(radio, "OperatingStandards"), "clients": clients_by_radio.get(key),
    } for key, radio in _instances(wifi.get("Radio"))]
    state["wifi_clients"] = total_clients
    # DSL: first line and channel.
    lines = _instances(_value_obj(root, "DSL", "Line"))
    if lines:
        line = lines[0][1]
        channels = _instances(_value_obj(root, "DSL", "Channel"))
        channel = channels[0][1] if channels else {}
        up = _up(_value(line, "Status")) and str(_value(line, "LinkStatus") or "").lower() == "up"
        state["dsl"] = {
            "status": _value(line, "Status"), "link_status": _value(line, "LinkStatus"), "standard": _value(line, "StandardUsed"),
            "down_bps": (_int(_value(channel, "DownstreamCurrRate")) or 0) * 1000 if up else None,
            "up_bps": (_int(_value(channel, "UpstreamCurrRate")) or 0) * 1000 if up else None,
            "snr_down_db": _tenths(_value(line, "DownstreamNoiseMargin")) if up else None,
            "attenuation_down_db": _tenths(_value(line, "DownstreamAttenuation")) if up else None,
        }
    return state


def _value_obj(node, *parts):
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


# --- TR-098 --------------------------------------------------------------------------------------

def _tr098(root: dict, cr_host: str | None) -> dict:
    state = {"data_model": "TR-098", "uptime": _int(_value(root, "DeviceInfo", "UpTime")), "cpu": None, "ram": None}
    connections = []
    for wan_key, wan_device in _instances(root.get("WANDevice")):
        for _cd_key, cd in _instances(wan_device.get("WANConnectionDevice")):
            for kind in ("WANPPPConnection", "WANIPConnection"):
                for _k, conn in _instances(cd.get(kind)):
                    connections.append({"wan": wan_key, "kind": kind, "node": conn, "ip": _ipv4(_value(conn, "ExternalIPAddress")),
                                        "status": _value(conn, "ConnectionStatus")})
    wan = (next((c for c in connections if cr_host and c["ip"] == cr_host), None)
           or next((c for c in connections if _up(c["status"])), None))
    if wan:
        node = wan["node"]
        state["wan"] = {"status": wan["status"], "type": "PPPoE" if wan["kind"] == "WANPPPConnection" else (_value(node, "AddressingType") or "IP"),
                        "ip": wan["ip"], "connected_for": _int(_value(node, "Uptime")), "last_error": _value(node, "LastConnectionError"),
                        "up": _up(wan["status"]),
                        "username": (str(_value(node, "Username") or "").strip() or None) if wan["kind"] == "WANPPPConnection" else None}
        common = _value_obj(root, "WANDevice", wan["wan"], "WANCommonInterfaceConfig") or {}
        state["counters"] = {"rx": _int(_value(common, "TotalBytesReceived")), "tx": _int(_value(common, "TotalBytesSent"))}
    radios, hosts, clients = [], None, None
    for _lan_key, lan in _instances(root.get("LANDevice")):
        count = _int(_value(lan, "Hosts", "HostNumberOfEntries"))
        if count is not None:
            hosts = (hosts or 0) + count
        for _w, wlan in _instances(lan.get("WLANConfiguration")):
            associated = _int(_value(wlan, "TotalAssociations"))
            if associated is not None:
                clients = (clients or 0) + associated
            channel = _int(_value(wlan, "Channel"))
            band = _value(wlan, "OperatingFrequencyBand") or ("5GHz" if channel and channel > 14 else "2.4GHz" if channel else None)
            radios.append({"band": band, "enabled": _value(wlan, "Enable"), "status": _value(wlan, "Status"), "channel": channel,
                           "bandwidth": _value(wlan, "OperatingChannelBandwidth"), "standards": _value(wlan, "Standard"), "clients": associated})
    state.update({"wifi": radios, "wifi_clients": clients, "lan_hosts": hosts})
    for _wan_key, wan_device in _instances(root.get("WANDevice")):
        dsl = wan_device.get("WANDSLInterfaceConfig")
        if isinstance(dsl, dict) and dsl:
            up = _up(_value(dsl, "Status"))
            state["dsl"] = {"status": _value(dsl, "Status"), "link_status": _value(dsl, "Status"), "standard": _value(dsl, "ModulationType"),
                            "down_bps": (_int(_value(dsl, "DownstreamCurrRate")) or 0) * 1000 if up else None,
                            "up_bps": (_int(_value(dsl, "UpstreamCurrRate")) or 0) * 1000 if up else None,
                            "snr_down_db": _tenths(_value(dsl, "DownstreamNoiseMargin")) if up else None,
                            "attenuation_down_db": _tenths(_value(dsl, "DownstreamAttenuation")) if up else None}
            break
    return state


# --- State, sample and status --------------------------------------------------------------------

def _parse_time(value) -> datetime | None:
    return acs._parse_seen(value)


def read_state(row: dict) -> dict:
    """Normalized state of one GenieACS device document (both data models)."""
    cr_url = acs._first_value(row, acs.CONNECTION_REQUEST_URL_PATHS)
    cr_host = _ipv4(urlsplit(str(cr_url)).hostname) if cr_url else None
    if isinstance(row.get("Device"), dict):
        state = _tr181(row["Device"], cr_host)
        interval = _int(_value(row, "Device", "ManagementServer", "PeriodicInformInterval"))
    else:
        state = _tr098(row.get("InternetGatewayDevice") or {}, cr_host)
        interval = _int(_value(row, "InternetGatewayDevice", "ManagementServer", "PeriodicInformInterval"))
    state["inform_interval"] = interval if interval and interval > 0 else None
    last_inform = _parse_time(row.get("_lastInform"))
    last_boot = _parse_time(row.get("_lastBoot"))
    state["last_inform"] = last_inform.isoformat() if last_inform else None
    state["last_boot"] = last_boot.isoformat() if last_boot else None
    state["nat"] = acs_port_mapping.parse(row)
    # Sent by the CPE at every Inform: its current WAN address, also with a dynamic IP (syslog attribution of TP-Link CPEs).
    state["wan_address"] = cr_host
    return state


def offline_after(state: dict) -> timedelta:
    interval = state.get("inform_interval") or DEFAULT_INFORM_SECONDS
    return max(MIN_OFFLINE_AFTER, timedelta(seconds=3 * min(interval, 86400)))


def _rates(state: dict, previous: dict | None, at: datetime) -> tuple[int | None, int | None, bool | None]:
    """WAN bits per second from the byte counters of two Informs; (rx, tx, counters reported)."""
    counters = state.get("counters") or {}
    rx, tx = counters.get("rx"), counters.get("tx")
    if rx is None or tx is None:
        return None, None, None
    wan_up = (state.get("wan") or {}).get("up")
    if rx == 0 and tx == 0 and wan_up and (state.get("uptime") or 0) >= ZERO_COUNTER_GRACE_SECONDS:
        return None, None, False
    prev = (previous or {}).get("counters") or {}
    prev_at = _parse_time(prev.get("at"))
    if prev_at is None or prev.get("rx") is None or prev.get("tx") is None:
        return None, None, True
    seconds = (at - prev_at).total_seconds()
    if seconds <= 0 or rx < prev["rx"] or tx < prev["tx"]:  # counter reset (reboot) or clock skew
        return None, None, True
    return int((rx - prev["rx"]) * 8 / seconds), int((tx - prev["tx"]) * 8 / seconds), True


def apply_state(db, device: Device, state: dict, now: datetime) -> bool:
    """Store the state on the device, update online/offline, add a sample for a new Inform. True when a sample was added."""
    data = dict(device.inventory_data or {})
    tr = dict(data.get("tr069") or {})
    previous = tr.get("monitor") if isinstance(tr.get("monitor"), dict) else None
    if state.get("nat") is None:
        state["nat"] = (previous or {}).get("nat")  # not read this time: keep the last one
    else:
        acs_port_mapping.record(db, device, (previous or {}).get("nat"), state["nat"])
    last_inform = _parse_time(state.get("last_inform"))
    added = False
    if last_inform is not None:
        device.last_seen = max(filter(None, (device.last_seen, last_inform)))
        device.status = "online" if now - last_inform <= offline_after(state) else "offline"
        tr["last_inform"] = last_inform.isoformat()
        newer = previous is None or (_parse_time(previous.get("last_inform")) or datetime.min.replace(tzinfo=last_inform.tzinfo)) < last_inform
        if newer:
            rx_bps, tx_bps, reported = _rates(state, previous, last_inform)
            state.setdefault("wan", None)
            if state.get("wan") is not None:
                state["wan"]["counters_reported"] = reported
            last_sample = db.scalar(select(func.max(AcsMetricSample.observed_at)).where(AcsMetricSample.device_id == device.id))
            if last_sample is None or last_sample < last_inform:
                dsl = state.get("dsl") or {}
                db.add(AcsMetricSample(
                    device_id=device.id, observed_at=last_inform, uptime_seconds=state.get("uptime"), cpu_percent=state.get("cpu"),
                    ram_percent=state.get("ram"), wan_rx_bps=rx_bps, wan_tx_bps=tx_bps, wifi_clients=state.get("wifi_clients"),
                    lan_hosts=state.get("lan_hosts"), dsl_down_bps=dsl.get("down_bps"), dsl_up_bps=dsl.get("up_bps"),
                    dsl_snr_down_db=dsl.get("snr_down_db"), dsl_attenuation_down_db=dsl.get("attenuation_down_db"),
                ))
                added = True
            counters = state.get("counters") or {}
            state["counters"] = {**counters, "at": last_inform.isoformat()}
        else:
            state["counters"] = (previous or {}).get("counters")
            if state.get("wan") is not None:
                state["wan"]["counters_reported"] = ((previous or {}).get("wan") or {}).get("counters_reported")
    state["read_at"] = now.isoformat()
    tr["monitor"] = state
    data["tr069"] = tr
    device.inventory_data = data
    return added


def collect(now: datetime | None = None, force: bool = False) -> dict:
    """Worker task: read the monitored values of every associated TR-069 CPE (at most every two minutes)."""
    global _last_run
    if not force and time.monotonic() - _last_run < RUN_INTERVAL_SECONDS:
        return {"status": "not_due"}
    _last_run = time.monotonic()
    now = now or utcnow()
    stats = {"status": "success", "devices": 0, "samples": 0, "missing": 0, "online": 0, "offline": 0}
    with SessionLocal() as db:
        connection = acs._connection(db)
        if connection is None or not connection.is_enabled:
            return {"status": "disabled"}
        devices = list(db.scalars(select(Device).where(Device.management_source == "tr069", Device.external_device_id.is_not(None))))
        try:
            for start in range(0, len(devices), BATCH):
                chunk = devices[start:start + BATCH]
                rows = acs._http_get(connection, query={"_id": {"$in": [d.external_device_id for d in chunk]}}, projection=PROJECTION)
                by_id = {str(row.get("_id")): row for row in rows}
                for device in chunk:
                    row = by_id.get(device.external_device_id)
                    if row is None:
                        stats["missing"] += 1
                        continue
                    stats["devices"] += 1
                    stats["samples"] += int(apply_state(db, device, read_state(row), now))
                    if device.status in ("online", "offline"):
                        stats[device.status] += 1
        except acs.GenieAcsConnectorError as exc:
            db.rollback()
            return {"status": "failed", "error": str(exc)}
        db.commit()
    return stats


def cleanup(now: datetime | None = None) -> int:
    now = now or utcnow()
    with SessionLocal() as db:
        deleted = expire_keep_latest(db, AcsMetricSample, now - timedelta(days=RETENTION_DAYS), "device_id")
        db.commit()
        return deleted


def series(db, device: Device, range_key: str = "24h") -> dict:
    """Same chart format as the UISP and cnMaestro graphs."""
    from app.uisp_metrics import _bucket

    delta = RANGES.get(range_key)
    if delta is None:
        raise ValueError("range")
    samples = list(db.scalars(select(AcsMetricSample).where(AcsMetricSample.device_id == device.id, AcsMetricSample.observed_at >= utcnow() - delta)
                              .order_by(AcsMetricSample.observed_at)))
    charts = []
    for key, title, unit, fields in CHARTS:
        names = [field for field, _label in fields]
        if not any(getattr(s, f) is not None for s in samples for f in names):
            continue
        points = _bucket(samples, names)
        charts.append({"id": key, "title": title, "unit": unit,
                       "series": [{"field": f, "label": label, "points": [[p["t"], p[f]] for p in points]} for f, label in fields]})
    return {"range": range_key, "sample_count": len(samples), "charts": charts}


def current(device) -> dict | None:
    """CPU, RAM and uptime of the last Inform read from GenieACS, when recent enough to be shown as current."""
    state = ((device.inventory_data or {}).get("tr069") or {}).get("monitor") or {}
    last_inform = _parse_time(state.get("last_inform"))
    if last_inform is None or utcnow() - last_inform > CURRENT_MAX_AGE:
        return None
    return {"cpu_percent": state.get("cpu"), "ram_percent": state.get("ram"), "uptime_seconds": state.get("uptime")}


STATE_KEYS = ("data_model", "uptime", "cpu", "ram", "wan", "wifi", "wifi_clients", "lan_hosts", "dsl", "inform_interval", "last_inform", "last_boot", "read_at")


@router.get("/devices/{device_id}/acs/monitor")
def device_acs_monitor(request: Request, device_id: uuid.UUID, range: str = "24h"):
    """State of the CPE at its last Inform and the charts of the selected range."""
    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        device = db.get(Device, device_id)
        if device is None or not acs.tr069_capable(device):
            raise HTTPException(404)
        state = ((device.inventory_data or {}).get("tr069") or {}).get("monitor") or None
        try:
            charts = series(db, device, range)
        except ValueError:
            raise HTTPException(400, {"code": "range", "message": "Intervallo non valido."}) from None
        return {"state": {key: state.get(key) for key in STATE_KEYS} if state else None, "online_window_minutes":
                int(offline_after(state or {}).total_seconds() // 60), **charts}


def install_acs_monitoring(app) -> None:
    app.include_router(router)
