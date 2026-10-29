"""Parameter profiles of TR-069 CPEs (ACS-04): the one map of the TR-098 / TR-181 paths NSM uses.

Before this module the paths were spread over the ACS modules (diagnostics, speed test, Wi-Fi, syslog,
firmware, port mapping, PON…). They now live here, per data model, with vendor additions only where a
feature exists only as a vendor extension (TP-Link ``X_TP_*``), and with **explicit unsupported entries**
that say why NSM does not offer a feature for a data model. The ACS modules take their object paths from
``PATHS``.

``capabilities(record, …)`` evaluates one CPE: for every NSM capability (monitoring, Wi-Fi, diagnostics,
speed test, firmware, syslog, port mapping, PON, snapshot…) whether it is available and why, from the
GenieACS record read with ``PROBES`` (one read-only NBI GET). Nothing is written to the CPE.

Statuses: ``available`` (the CPE exposes the parameters), ``rpc`` (a standard TR-069 RPC every CPE
implements: Download, Reboot), ``not_exposed`` (the data model has the parameters but this CPE does not
report them, or GenieACS has not read them yet), ``unsupported`` (no such parameter in this data model, or
NSM does not implement it for this data model; ``reason`` says which).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from app import acs_pon
from app import genieacs_connector as acs
from app.backup_models import BackupArtifact
from app.db import SessionLocal
from app.models import BackupRun, Device
from app.ui_api import ui_user

ROOT = {"tr181": "Device", "tr098": "InternetGatewayDevice"}

# Object paths used by the ACS modules, per data model. A missing key means "not available in this data model".
PATHS: dict[str, dict[str, str]] = {
    "tr181": {
        "software_version": "Device.DeviceInfo.SoftwareVersion",
        "cpu": "Device.DeviceInfo.ProcessStatus.CPUUsage",
        "memory": "Device.DeviceInfo.MemoryStatus",
        "inform_interval": "Device.ManagementServer.PeriodicInformInterval",
        "acs_url": "Device.ManagementServer.URL",
        "wan_ip": "Device.IP.Interface",
        "wan_ppp": "Device.PPP.Interface",
        "wifi": "Device.WiFi",
        "wifi_radio": "Device.WiFi.Radio",
        "dsl": "Device.DSL.Line",
        "optical": "Device.Optical",
        "ping": "Device.IP.Diagnostics.IPPing",
        "traceroute": "Device.IP.Diagnostics.TraceRoute",
        "download": "Device.IP.Diagnostics.DownloadDiagnostics",
        "upload": "Device.IP.Diagnostics.UploadDiagnostics",
        "port_mapping": "Device.NAT.PortMapping",
        "upnp": "Device.UPnP.Device.Enable",
        "remote_access": "Device.UserInterface.RemoteAccess",
    },
    "tr098": {
        "software_version": "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
        "cpu": "InternetGatewayDevice.DeviceInfo.ProcessStatus.CPUUsage",
        "memory": "InternetGatewayDevice.DeviceInfo.MemoryStatus",
        "inform_interval": "InternetGatewayDevice.ManagementServer.PeriodicInformInterval",
        "acs_url": "InternetGatewayDevice.ManagementServer.URL",
        "wan": "InternetGatewayDevice.WANDevice",
        "wifi": "InternetGatewayDevice.LANDevice.1.WLANConfiguration",
        "ping": "InternetGatewayDevice.IPPingDiagnostics",
        "traceroute": "InternetGatewayDevice.TraceRouteDiagnostics",
        "download": "InternetGatewayDevice.DownloadDiagnostics",
        "upload": "InternetGatewayDevice.UploadDiagnostics",
        "remote_access": "InternetGatewayDevice.UserInterface.RemoteAccess",
    },
}
# Vendor extensions, only for features that exist only there.
VENDOR_PATHS: dict[str, dict[str, str]] = {
    "tp-link": {
        "syslog": "Device.DeviceInfo.X_TP_LogCfg",
        "tplink_cloud": "Device.X_TP_CloudService",
        "upnp_vendor": "Device.NAT.X_TP_UPnPCfg.Enable",
    },
}
# Why a capability is not offered for a data model (shown on the CPE, never guessed).
UNSUPPORTED: dict[str, dict[str, str]] = {
    "tr098": {
        "resources": "nsm_tr098",          # TR-098 CPUUsage/MemoryStatus exist on few CPEs; NSM reads them on TR-181 only
        "syslog": "vendor_tr181",          # X_TP_LogCfg exists only on TP-Link TR-181 (Aginet) firmware
        "firmware_cloud": "vendor_tr181",  # X_TP_CloudService likewise
        "upnp": "no_standard",             # no standard UPnP switch in TR-098
    },
    "*": {
        "config_file": "no_upload_task",   # GenieACS has no TR-069 Upload task for the vendor configuration file
    },
}


def path(model: str, key: str) -> str:
    """The object path of ``key`` for a data model (``tr181`` / ``tr098``); KeyError when the model has none."""
    return PATHS[model][key]


def vendor_path(vendor: str, key: str) -> str:
    return VENDOR_PATHS[vendor][key]


def data_model(record: dict) -> str | None:
    if isinstance(record.get("Device"), dict):
        return "tr181"
    if isinstance(record.get("InternetGatewayDevice"), dict):
        return "tr098"
    return None


def vendor_of(record: dict) -> str | None:
    manufacturer = str(((record.get("_deviceId") or {}).get("_Manufacturer")) or "").lower()
    return "tp-link" if manufacturer.startswith("tp-l") else None


# Leaves (or small objects) read once to evaluate a CPE: never the whole tree.
PROBES = (
    "_id", "_deviceId", "_lastInform",
    "Device.DeviceInfo.SoftwareVersion", "Device.DeviceInfo.ProcessStatus.CPUUsage", "Device.DeviceInfo.MemoryStatus.Total",
    "Device.ManagementServer.PeriodicInformInterval", "Device.ManagementServer.URL",
    "Device.IP.InterfaceNumberOfEntries", "Device.PPP.InterfaceNumberOfEntries", "Device.WiFi.RadioNumberOfEntries",
    "Device.WiFi.SSIDNumberOfEntries", "Device.DSL.Line", "Device.Optical",
    "Device.IP.Diagnostics.IPPing.DiagnosticsState", "Device.IP.Diagnostics.TraceRoute.DiagnosticsState",
    "Device.IP.Diagnostics.DownloadDiagnostics.DiagnosticsState", "Device.IP.Diagnostics.UploadDiagnostics.DiagnosticsState",
    "Device.NAT.PortMappingNumberOfEntries", "Device.UPnP.Device.Enable", "Device.UserInterface.RemoteAccess.Enable",
    "Device.DeviceInfo.X_TP_LogCfg.ServerIP", "Device.X_TP_CloudService.FwUpgradeInfo.Version", "Device.NAT.X_TP_UPnPCfg.Enable",
    "InternetGatewayDevice.DeviceInfo.SoftwareVersion", "InternetGatewayDevice.DeviceInfo.ProcessStatus.CPUUsage",
    "InternetGatewayDevice.ManagementServer.PeriodicInformInterval", "InternetGatewayDevice.ManagementServer.URL",
    "InternetGatewayDevice.WANDevice", "InternetGatewayDevice.LANDevice.1.WLANConfiguration",
    "InternetGatewayDevice.IPPingDiagnostics.DiagnosticsState", "InternetGatewayDevice.TraceRouteDiagnostics.DiagnosticsState",
    "InternetGatewayDevice.DownloadDiagnostics.DiagnosticsState", "InternetGatewayDevice.UploadDiagnostics.DiagnosticsState",
    "InternetGatewayDevice.UserInterface.RemoteAccess.Enable",
)


# --- Evaluation ----------------------------------------------------------------------------------

def _node(record: dict, dotted: str):
    node = record
    for part in dotted.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def _has(record: dict, dotted: str) -> bool:
    node = _node(record, dotted)
    if not isinstance(node, dict):
        return False
    if dotted.endswith("NumberOfEntries") and dotted != "Device.NAT.PortMappingNumberOfEntries":
        try:
            return int(node.get("_value")) > 0
        except (TypeError, ValueError):
            return False
    return bool([k for k in node if not k.startswith("_")] or "_value" in node)


def _instances(node) -> list[dict]:
    if not isinstance(node, dict):
        return []
    return [node[k] for k in sorted((k for k in node if k.isdigit() and isinstance(node[k], dict)), key=int)]


def _wan_connections(record: dict) -> list[dict]:
    out = []
    for wan in _instances(_node(record, "InternetGatewayDevice.WANDevice")):
        for cd in _instances(wan.get("WANConnectionDevice")):
            for kind in ("WANPPPConnection", "WANIPConnection"):
                out.extend(_instances(cd.get(kind)))
    return out


def _tr098_dsl(record: dict) -> bool:
    return any(isinstance(wan.get("WANDSLInterfaceConfig"), dict) and wan["WANDSLInterfaceConfig"]
               for wan in _instances(_node(record, "InternetGatewayDevice.WANDevice")))


def _entry(cap: str, status: str, paths=(), reason: str | None = None, writes: bool = False, where: str | None = None) -> dict:
    return {"id": cap, "status": status, "reason": reason, "paths": [p for p in paths if p], "writes": writes, "where": where}


def _probe(record: dict, cap: str, paths: list[str], writes=False, where=None, reason_missing="not_reported") -> dict:
    found = [p for p in paths if _has(record, p)]
    return _entry(cap, "available" if found else "not_exposed", found or paths, None if found else reason_missing, writes, where)


def capabilities(record: dict, snapshot_count: int = 0) -> dict:
    """What NSM can do on this CPE and why, from a GenieACS record read with ``PROBES``."""
    model = data_model(record)
    vendor = vendor_of(record)
    caps: list[dict] = []
    if model is None:
        return {"data_model": None, "vendor_profile": vendor, "capabilities": [], "reason": "not_read"}
    p = PATHS[model]
    unsupported = {**UNSUPPORTED["*"], **UNSUPPORTED.get(model, {})}

    def unsup(cap: str, where=None):
        caps.append(_entry(cap, "unsupported", (), unsupported[cap], False, where))

    caps.append(_probe(record, "identity", [p["software_version"]], where="device"))
    if "resources" in unsupported:
        unsup("resources", "monitor")
    else:
        caps.append(_probe(record, "resources", [p["cpu"], f'{p["memory"]}.Total'], where="monitor"))
    if model == "tr181":
        caps.append(_probe(record, "wan", [f'{p["wan_ppp"]}NumberOfEntries', f'{p["wan_ip"]}NumberOfEntries'], where="monitor"))
        caps.append(_probe(record, "wifi", [f'{p["wifi_radio"]}NumberOfEntries', "Device.WiFi.SSIDNumberOfEntries"], writes=True, where="wifi"))
        caps.append(_probe(record, "dsl", [p["dsl"]], where="monitor", reason_missing="no_dsl"))
    else:
        conns = _wan_connections(record)
        caps.append(_entry("wan", "available" if conns else "not_exposed", [p["wan"]], None if conns else "not_reported", where="monitor"))
        caps.append(_probe(record, "wifi", [p["wifi"]], writes=True, where="wifi"))
        dsl = _tr098_dsl(record)
        caps.append(_entry("dsl", "available" if dsl else "not_exposed", [f'{p["wan"]}.*.WANDSLInterfaceConfig'], None if dsl else "no_dsl",
                           where="monitor"))
    pon = acs_pon.parse(record)
    caps.append(_entry("pon", "available" if pon else "not_exposed", [pon["source"]] if pon else (
        [p["optical"]] if model == "tr181" else [f'{p["wan"]}.*.X_GponInterafceConfig', f'{p["wan"]}.*.WANPONInterfaceConfig']),
        None if pon else "no_pon", where="monitor"))
    for cap in ("ping", "traceroute"):
        caps.append(_probe(record, cap, [f"{p[cap]}.DiagnosticsState"], writes=True, where="diagnostics"))
    caps.append(_probe(record, "speedtest", [f'{p["download"]}.DiagnosticsState', f'{p["upload"]}.DiagnosticsState'], writes=True,
                       where="speedtest"))
    caps.append(_entry("firmware", "rpc", (), "download_rpc", True, "firmware"))
    if "firmware_cloud" in unsupported:
        unsup("firmware_cloud", "firmware")
    elif vendor == "tp-link":
        caps.append(_probe(record, "firmware_cloud", [f'{vendor_path("tp-link", "tplink_cloud")}.FwUpgradeInfo.Version'], writes=True,
                           where="firmware"))
    else:
        caps.append(_entry("firmware_cloud", "unsupported", (), "vendor_tplink", where="firmware"))
    if "syslog" in unsupported:
        unsup("syslog", "syslog")
    elif vendor == "tp-link":
        caps.append(_probe(record, "syslog", [f'{vendor_path("tp-link", "syslog")}.ServerIP'], writes=True, where="syslog"))
    else:
        caps.append(_entry("syslog", "unsupported", (), "vendor_tplink", where="syslog"))
    if model == "tr181":
        caps.append(_probe(record, "port_mapping", ["Device.NAT.PortMappingNumberOfEntries"], where="nat"))
        upnp_paths = [p["upnp"]] + ([vendor_path("tp-link", "upnp_vendor")] if vendor == "tp-link" else [])
        caps.append(_probe(record, "upnp", upnp_paths, where="nat"))
    else:
        conns = _wan_connections(record)
        mapping = any("PortMapping" in conn or "PortMappingNumberOfEntries" in conn for conn in conns)
        caps.append(_entry("port_mapping", "available" if mapping else "not_exposed",
                           [f'{p["wan"]}.*.WANConnectionDevice.*.WAN(PPP|IP)Connection.*.PortMapping'], None if mapping else "not_reported",
                           where="nat"))
        unsup("upnp", "nat")
    caps.append(_probe(record, "remote_access", [f'{p["remote_access"]}.Enable'], where="exposure"))
    caps.append(_entry("reboot", "rpc", (), "reboot_rpc", True, "wifi"))
    caps.append(_probe(record, "inform_interval", [p["inform_interval"]], writes=True, where="provision"))
    caps.append(_entry("config_snapshot", "available", list(ROOT[m] for m in (model,)), "snapshots" if snapshot_count else "no_snapshot",
                       where="snapshot"))
    caps.append(_entry("config_restore", "available" if snapshot_count else "not_exposed", (), "restore_preview" if snapshot_count else "no_snapshot",
                       True, "snapshot"))
    unsup("config_file", "snapshot")
    return {"data_model": model, "vendor_profile": vendor, "capabilities": caps, "reason": None}


# --- Route ---------------------------------------------------------------------------------------

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])


@router.get("/devices/{device_id}/acs/capabilities")
def device_acs_capabilities(request: Request, device_id: uuid.UUID):
    """What NSM can do on the CPE and why (one read-only GenieACS read; nothing is sent to the CPE)."""
    # Imported here: these modules take their paths from this one.
    from app import acs_diagnostics as nbi
    from app.acs_snapshot import ARTIFACT_TYPE

    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        device = db.get(Device, device_id)
        if device is None or not acs.tr069_capable(device):
            raise HTTPException(404)
        connection = acs._connection(db)
        if not device.external_device_id or connection is None or not connection.is_enabled:
            raise HTTPException(409, {"code": "not_linked", "message": "Collega prima il CPE a GenieACS (scheda ACS)."})
        snapshots = db.scalar(select(func.count(BackupArtifact.id)).join(BackupRun, BackupRun.id == BackupArtifact.run_id).where(
            BackupRun.device_id == device.id, BackupArtifact.artifact_type == ARTIFACT_TYPE, BackupArtifact.deleted_at.is_(None))) or 0
        try:
            record = nbi._read(connection, device.external_device_id, list(PROBES))
        except nbi.AcsDiagnosticError as exc:
            raise HTTPException(502, {"code": "acs", "message": str(exc)}) from None
        ident = record.get("_deviceId") or {}
        return {**capabilities(record, snapshot_count=int(snapshots)), "manufacturer": ident.get("_Manufacturer"),
                "product_class": ident.get("_ProductClass"), "last_inform": record.get("_lastInform"), "snapshots": int(snapshots)}


def install_acs_profiles(app) -> None:
    app.include_router(router)
