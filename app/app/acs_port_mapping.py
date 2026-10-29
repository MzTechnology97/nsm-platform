"""Port forwarding, DMZ and UPnP of TR-069 CPEs, monitoring only (operator decision 2026-10-23).

The monitoring read (``acs_monitoring``) also parses, from the GenieACS record:

- port mappings: TR-181 ``Device.NAT.PortMapping.{i}``, TR-098
  ``WANDevice.*.WANConnectionDevice.*.WAN(PPP|IP)Connection.*.PortMapping.{i}``;
- DMZ: any vendor object whose name contains ``DMZ`` under ``Device.NAT``/``Device.Firewall`` or a TR-098 WAN
  connection (e.g. ``X_TP_DMZ``), with its enable flag and host;
- UPnP on/off: ``Device.UPnP.Device.Enable`` or a vendor flag (TP-Link ``Device.NAT.X_TP_UPnPCfg.Enable``); the path of
  the flag is kept (``upnp_path``) so that UPnP can be turned off from NSM (``upnp_control``, ACCESS-02).

The result is kept in ``inventory_data["tr069"]["monitor"]["nat"]`` and shown on the *ACS* tab. When the
mappings, the DMZ or UPnP change between two reads NSM records ``ACS_PORT_MAPPING_CHANGED`` with what was added
or removed: a new port forwarding exposes a customer host to the Internet. Nothing is ever changed on the CPE;
*Rileggi dal CPE* (``devices.write``) asks GenieACS to re-read ``Device.NAT``. The NSM base provision re-reads
these objects every hour.
"""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app import acs_diagnostics as nbi
from app import genieacs_connector as acs
from app import main as core
from app.db import SessionLocal
from app.models import Device
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
PROJECTION = ("Device.NAT", "Device.UPnP.Device.Enable")
MAX_MAPPINGS = 200
_DMZ = re.compile(r"dmz", re.I)
_UPNP = re.compile(r"upnp", re.I)


def _value(node, name):
    leaf = node.get(name) if isinstance(node, dict) else None
    return leaf.get("_value") if isinstance(leaf, dict) else None


def _instances(node) -> list[tuple[str, dict]]:
    if not isinstance(node, dict):
        return []
    return sorted(((k, v) for k, v in node.items() if k.isdigit() and isinstance(v, dict)), key=lambda kv: int(kv[0]))


def _port(value) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def _flag(value) -> bool | None:
    if value is None:
        return None
    return str(value).strip().lower() in ("true", "1", "enabled", "on")


def _mapping(node: dict, names: dict) -> dict:
    external = _port(_value(node, names["external"]))
    end = _port(_value(node, "ExternalPortEndRange"))
    return {"enabled": _flag(_value(node, names["enable"])), "protocol": str(_value(node, names["protocol"]) or "").upper()[:10] or None,
            "external_port": external, "external_port_end": end if end and external and end > external else None,
            "internal_port": _port(_value(node, "InternalPort")), "internal_client": str(_value(node, "InternalClient") or "").strip()[:64] or None,
            "remote_host": str(_value(node, "RemoteHost") or "").strip()[:64] or None,
            "description": str(_value(node, names["description"]) or "").strip()[:120] or None}


TR181_NAMES = {"enable": "Enable", "protocol": "Protocol", "external": "ExternalPort", "description": "Description"}
TR098_NAMES = {"enable": "PortMappingEnabled", "protocol": "PortMappingProtocol", "external": "ExternalPort", "description": "PortMappingDescription"}


def _vendor_objects(node, pattern, depth=0, path=""):
    """(path, object) of the children whose name matches ``pattern`` (vendor extensions), a few levels deep."""
    if not isinstance(node, dict) or depth > 3:
        return []
    found = []
    for key, child in node.items():
        if key.startswith("_") or not isinstance(child, dict) or "_value" in child:
            continue
        if pattern.search(key):
            found.append((f"{path}{key}", child))
        found += _vendor_objects(child, pattern, depth + 1, f"{path}{key}.")
    return found


def _dmz(candidates) -> dict | None:
    for path, node in candidates:
        enabled = _flag(next((_value(node, n) for n in ("Enable", "Enabled", "DMZEnable", "X_TP_Enable") if _value(node, n) is not None), None))
        host = next((str(_value(node, n)).strip() for n in ("HostIPAddress", "HostIP", "IPAddress", "InternalClient", "Host", "DMZHostIPAddress")
                     if _value(node, n) not in (None, "")), None)
        if enabled is not None or host:
            return {"enabled": bool(enabled), "host": host[:64] if host else None, "path": path}
    return None


def _upnp(candidates) -> tuple[bool | None, str | None]:
    """UPnP on/off and the path of the flag that says it."""
    for path, node in candidates:
        name = next((n for n in ("Enable", "Enabled") if _value(node, n) is not None), None)
        if name:
            return _flag(_value(node, name)), f"{path}.{name}"
    return None, None


def parse(row: dict) -> dict | None:
    """Port mappings, DMZ and UPnP of one GenieACS record; None when GenieACS has not read them."""
    root = row.get("Device")
    if isinstance(root, dict):
        nat = root.get("NAT")
        if not isinstance(nat, dict) or not any(not k.startswith("_") for k in nat):
            return None
        mappings = [_mapping(node, TR181_NAMES) for _k, node in _instances(nat.get("PortMapping"))]
        dmz = _dmz(_vendor_objects(nat, _DMZ, path="Device.NAT.") + _vendor_objects(root.get("Firewall"), _DMZ, path="Device.Firewall."))
        upnp, upnp_path = _flag(_value((root.get("UPnP") or {}).get("Device") or {}, "Enable")), "Device.UPnP.Device.Enable"
        if upnp is None:
            upnp, upnp_path = _upnp(_vendor_objects(nat, _UPNP, path="Device.NAT."))
        return {"mappings": mappings[:MAX_MAPPINGS], "dmz": dmz, "upnp": upnp, "upnp_path": upnp_path}
    root = row.get("InternetGatewayDevice")
    if not isinstance(root, dict):
        return None
    mappings, dmz_candidates, seen = [], [], False
    for wan_key, wan in _instances(root.get("WANDevice")):
        for cd_key, cd in _instances(wan.get("WANConnectionDevice")):
            for kind in ("WANPPPConnection", "WANIPConnection"):
                for conn_key, conn in _instances(cd.get(kind)):
                    if "PortMapping" in conn or "PortMappingNumberOfEntries" in conn:
                        seen = True
                    mappings += [_mapping(node, TR098_NAMES) for _k, node in _instances(conn.get("PortMapping"))]
                    dmz_candidates += _vendor_objects(conn, _DMZ, path=f"InternetGatewayDevice.WANDevice.{wan_key}.WANConnectionDevice.{cd_key}.{kind}.{conn_key}.")
    if not seen:
        return None
    upnp, upnp_path = _upnp(_vendor_objects(root, _UPNP, path="InternetGatewayDevice."))
    return {"mappings": mappings[:MAX_MAPPINGS], "dmz": _dmz(dmz_candidates), "upnp": upnp, "upnp_path": upnp_path}


def _key(mapping: dict) -> str:
    end = f"-{mapping['external_port_end']}" if mapping.get("external_port_end") else ""
    return (f"{mapping.get('protocol') or '*'} {mapping.get('external_port') or '*'}{end} → {mapping.get('internal_client') or '?'}:"
            f"{mapping.get('internal_port') or mapping.get('external_port') or '*'}" + ("" if mapping.get("enabled") is not False else " (disattivata)"))


def changes(previous: dict | None, current: dict | None) -> dict | None:
    """What changed between two reads (None on the first read or when nothing changed)."""
    if not previous or not current:
        return None
    before, after = {_key(m) for m in previous.get("mappings") or []}, {_key(m) for m in current.get("mappings") or []}
    out = {}
    if after - before:
        out["added"] = sorted(after - before)
    if before - after:
        out["removed"] = sorted(before - after)
    if (previous.get("dmz") or {}) != (current.get("dmz") or {}):
        out["dmz"] = current.get("dmz")
    if previous.get("upnp") != current.get("upnp"):
        out["upnp"] = current.get("upnp")
    return out or None


def record(db, device, previous: dict | None, current: dict | None) -> None:
    """Audit a change of the port forwarding, DMZ or UPnP of a CPE (caller commits)."""
    change = changes(previous, current)
    if change:
        core.add_event(db, "ACS_PORT_MAPPING_CHANGED", customer_id=device.customer_id, device_id=device.id, source="worker",
                       severity="warning" if change.get("added") or (change.get("dmz") or {}).get("enabled") or change.get("upnp") else "info",
                       details=change)


def _payload(device, can_refresh: bool) -> dict:
    state = ((device.inventory_data or {}).get("tr069") or {}).get("monitor") or {}
    nat = state.get("nat")
    active = [m for m in (nat or {}).get("mappings") or [] if m.get("enabled") is not False]
    return {"nat": nat, "read_at": state.get("read_at"), "open": len(active), "can_refresh": can_refresh}


@router.get("/devices/{device_id}/acs/port-mapping")
def device_acs_port_mapping(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device = db.get(Device, device_id)
        if device is None or not acs.tr069_capable(device):
            raise HTTPException(404)
        return _payload(device, core.has_permission(user, "devices.write"))


@router.post("/devices/{device_id}/acs/port-mapping/refresh", dependencies=[Depends(require_csrf)])
def device_acs_port_mapping_refresh(request: Request, device_id: uuid.UUID):
    """Ask GenieACS to re-read the NAT object from the CPE (a read: nothing is changed on the CPE)."""
    with SessionLocal() as db:
        ui_user(request, db, "devices.write")
        device, connection = nbi._linked(db, device_id)
        try:
            record_row = nbi._read(connection, device.external_device_id, ["Device.NAT", "InternetGatewayDevice.WANDevice"])
            target = "Device.NAT" if isinstance(record_row.get("Device"), dict) else "InternetGatewayDevice.WANDevice"
            started = nbi._task(connection, device.external_device_id, {"name": "refreshObject", "objectName": target})
        except nbi.AcsDiagnosticError as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        return {"started": started}


def install_acs_port_mapping(app) -> None:
    app.include_router(router)
