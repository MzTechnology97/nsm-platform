"""Wi-Fi settings and reboot of TR-069 CPEs from NSM, through GenieACS (operator decision 2026-10-22).

- ``GET /api/ui/v1/devices/{id}/acs/wifi`` (``devices.read``): radios (band, channel,
  automatic channel, allowed channels) and Wi-Fi networks (SSID, enabled, security,
  band) as GenieACS last read them; ``?refresh=1`` (``devices.write``) asks GenieACS
  to re-read the Wi-Fi object from the CPE first. Passwords are never readable:
  CPEs do not report them.
- ``POST .../acs/wifi`` (``devices.write``) with ``networks`` (``ssid``, ``password``,
  ``enabled``) and ``radios`` (``channel``: number or ``"auto"``, ``enabled``):
  validated, sent as one ``setParameterValues`` task with a connection request,
  audited ``ACS_WIFI_CHANGED`` (names of the changed fields only, never the
  password).
- ``POST .../acs/reboot`` (``devices.write``) with the device name typed as
  confirmation: GenieACS ``reboot`` task, audited ``ACS_REBOOT_REQUESTED``; the
  reboot is confirmed when GenieACS reports a later boot (``_lastBoot``) and
  audited ``ACS_REBOOT_VERIFIED`` by ``GET .../acs/reboot``.

Both data models are handled: TR-181 (``Device.WiFi.Radio/SSID/AccessPoint``) and
TR-098 (``InternetGatewayDevice.LANDevice.1.WLANConfiguration``).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import acs_profiles as profiles
from app import acs_diagnostics as nbi
from app import genieacs_connector as acs
from app import main as core
from app.db import SessionLocal
from app.models import Device, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
SSID_RE = re.compile(r"^[\x20-\x7e]{1,32}$")
PASSWORD_RE = re.compile(r"^[\x20-\x7e]{8,63}$")
REBOOT_GIVE_UP = timedelta(minutes=15)
TR181_WIFI = profiles.path("tr181", "wifi")
TR098_WLAN = profiles.path("tr098", "wifi")


def _v(node, *parts):
    for part in parts:
        node = node.get(part) if isinstance(node, dict) else None
    return node.get("_value") if isinstance(node, dict) else None


def _w(node, *parts) -> bool:
    for part in parts:
        node = node.get(part) if isinstance(node, dict) else None
    return bool(isinstance(node, dict) and node.get("_writable"))


def _instances(node) -> list[tuple[str, dict]]:
    if not isinstance(node, dict):
        return []
    return [(k, node[k]) for k in sorted((k for k in node if k.isdigit() and isinstance(node[k], dict)), key=int)]


def _bool(value):
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return True if text in ("true", "1") else False if text in ("false", "0") else None


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _channels(value) -> list[int]:
    out = []
    for part in str(value or "").split(","):
        part = part.strip()
        if "-" in part:
            low, _, high = part.partition("-")
            if low.isdigit() and high.isdigit() and int(high) - int(low) < 200:
                out.extend(range(int(low), int(high) + 1))
        elif part.isdigit():
            out.append(int(part))
    return sorted(set(out))


def read_wifi(record: dict) -> dict:
    """Radios and networks of one GenieACS record (TR-181 or TR-098)."""
    wifi = (record.get("Device") or {}).get("WiFi")
    if isinstance(wifi, dict):
        radios = [{"index": key, "band": _v(node, "OperatingFrequencyBand"), "enabled": _bool(_v(node, "Enable")), "channel": _int(_v(node, "Channel")),
                   "auto_channel": _bool(_v(node, "AutoChannelEnable")), "possible_channels": _channels(_v(node, "PossibleChannels")),
                   "bandwidth": _v(node, "OperatingChannelBandwidth"), "writable": _w(node, "Channel")}
                  for key, node in _instances(wifi.get("Radio"))]
        bands = {r["index"]: r["band"] for r in radios}
        ssids = dict(_instances(wifi.get("SSID")))
        networks = []
        for key, ap in _instances(wifi.get("AccessPoint")):
            ssid_key = str(_v(ap, "SSIDReference") or "").rstrip(".").split(".")[-1]
            ssid = ssids.get(ssid_key) or {}
            radio = str(_v(ssid, "LowerLayers") or "").rstrip(".").split(".")[-1]
            networks.append({"index": key, "ssid_index": ssid_key or None, "ssid": _v(ssid, "SSID"), "enabled": _bool(_v(ap, "Enable")) and _bool(_v(ssid, "Enable")) is not False,
                             "security": _v(ap, "Security", "ModeEnabled"), "band": bands.get(radio), "radio": radio or None,
                             "broadcast": _bool(_v(ap, "SSIDAdvertisementEnabled")), "writable": _w(ssid, "SSID")})
        return {"model": "tr181", "radios": radios, "networks": networks}
    wlans = _instances(((((record.get("InternetGatewayDevice") or {}).get("LANDevice") or {}).get("1") or {}).get("WLANConfiguration")))
    if wlans:
        radios, networks = [], []
        for key, node in wlans:
            channel = _int(_v(node, "Channel"))
            band = _v(node, "OperatingFrequencyBand") or ("5GHz" if channel and channel > 14 else "2.4GHz" if channel else None)
            radios.append({"index": key, "band": band, "enabled": _bool(_v(node, "RadioEnabled")), "channel": channel, "auto_channel": _bool(_v(node, "AutoChannelEnable")),
                           "possible_channels": _channels(_v(node, "PossibleChannels")), "bandwidth": _v(node, "OperatingChannelBandwidth"), "writable": _w(node, "Channel")})
            networks.append({"index": key, "ssid_index": key, "ssid": _v(node, "SSID"), "enabled": _bool(_v(node, "Enable")), "security": _v(node, "BeaconType"), "band": band,
                             "radio": key, "broadcast": _bool(_v(node, "SSIDAdvertisementEnabled")), "writable": _w(node, "SSID")})
        return {"model": "tr098", "radios": radios, "networks": networks}
    return {"model": None, "radios": [], "networks": []}


def build_changes(current: dict, networks: list, radios: list) -> tuple[list, list]:
    """(parameterValues, audit field names) from the requested changes, validated against the CPE's current Wi-Fi."""
    model = current["model"]
    if model is None:
        raise HTTPException(409, {"code": "unsupported", "message": "Il CPE non espone il Wi-Fi via TR-069."})
    known_networks = {n["index"]: n for n in current["networks"]}
    known_radios = {r["index"]: r for r in current["radios"]}
    values, fields = [], []
    for change in networks or []:
        index = str((change or {}).get("index") or "")
        network = known_networks.get(index)
        if network is None:
            raise HTTPException(422, {"code": "invalid", "message": f"Rete Wi-Fi {index} sconosciuta."})
        if model == "tr181":
            if not network.get("ssid_index"):
                raise HTTPException(409, {"code": "unsupported", "message": f"La rete Wi-Fi {index} non indica il suo SSID."})
            ap = f"{TR181_WIFI}.AccessPoint.{index}"
            ssid_path = f"{TR181_WIFI}.SSID.{network['ssid_index']}"
            name_path, password_path, enable_path = f"{ssid_path}.SSID", f"{ap}.Security.KeyPassphrase", f"{ap}.Enable"
        else:
            base = f"{TR098_WLAN}.{index}"
            name_path, password_path, enable_path = f"{base}.SSID", f"{base}.KeyPassphrase", f"{base}.Enable"
        if "ssid" in change and change["ssid"] != network["ssid"]:
            name = str(change["ssid"] or "")
            if not SSID_RE.match(name):
                raise HTTPException(422, {"code": "invalid", "message": "Il nome della rete deve avere da 1 a 32 caratteri stampabili."})
            values.append([name_path, name, "xsd:string"])
            fields.append(f"network {index}: ssid")
        if change.get("password"):
            password = str(change["password"])
            if not PASSWORD_RE.match(password):
                raise HTTPException(422, {"code": "invalid", "message": "La password Wi-Fi deve avere da 8 a 63 caratteri stampabili."})
            values.append([password_path, password, "xsd:string"])
            fields.append(f"network {index}: password")
        if "enabled" in change and bool(change["enabled"]) != bool(network["enabled"]):
            values.append([enable_path, bool(change["enabled"]), "xsd:boolean"])
            fields.append(f"network {index}: {'enabled' if change['enabled'] else 'disabled'}")
    for change in radios or []:
        index = str((change or {}).get("index") or "")
        radio = known_radios.get(index)
        if radio is None:
            raise HTTPException(422, {"code": "invalid", "message": f"Radio {index} sconosciuta."})
        base = f"{TR181_WIFI}.Radio.{index}" if model == "tr181" else f"{TR098_WLAN}.{index}"
        if "channel" in change:
            channel = change["channel"]
            if channel == "auto":
                if not radio["auto_channel"]:
                    values.append([f"{base}.AutoChannelEnable", True, "xsd:boolean"])
                    fields.append(f"radio {index}: channel auto")
            else:
                number = _int(channel)
                if number is None or (radio["possible_channels"] and number not in radio["possible_channels"]):
                    raise HTTPException(422, {"code": "invalid", "message": f"Canale {channel} non consentito su questa radio."})
                if radio["auto_channel"] or number != radio["channel"]:
                    values += [[f"{base}.AutoChannelEnable", False, "xsd:boolean"], [f"{base}.Channel", number, "xsd:unsignedInt"]]
                    fields.append(f"radio {index}: channel {number}")
        if "enabled" in change and bool(change["enabled"]) != bool(radio["enabled"]):
            values.append([f"{base}.Enable" if model == "tr181" else f"{base}.RadioEnabled", bool(change["enabled"]), "xsd:boolean"])
            fields.append(f"radio {index}: {'enabled' if change['enabled'] else 'disabled'}")
    if not values:
        raise HTTPException(422, {"code": "unchanged", "message": "Nessuna modifica da inviare."})
    return values, fields


# --- Records -----------------------------------------------------------------------------------

def _wifi_paths(model_hint: str | None) -> list[str]:
    return [TR181_WIFI, TR098_WLAN] if model_hint is None else [TR181_WIFI] if model_hint == "tr181" else [TR098_WLAN]


def _linked(db, device_id):
    device = db.get(Device, device_id)
    if device is None or not acs.tr069_capable(device):
        raise HTTPException(404)
    connection = acs._connection(db)
    if not device.external_device_id or connection is None or not connection.is_enabled:
        raise HTTPException(409, {"code": "not_linked", "message": "Collega prima il CPE a GenieACS (scheda ACS)."})
    return device, connection


def _boot(record: dict):
    return acs._parse_seen(record.get("_lastBoot"))


def reboot_state(db, device, connection, now: datetime | None = None) -> dict | None:
    """The last reboot requested from NSM, confirmed once GenieACS reports a later boot (audited once). Caller commits."""
    now = now or utcnow()
    data = dict(device.inventory_data or {})
    entry = dict(data.get("acs_reboot") or {})
    if not entry or entry.get("state") != "requested":
        return entry or None
    record = nbi._read(connection, device.external_device_id, ["_lastBoot"])
    boot = _boot(record)
    requested = datetime.fromisoformat(entry["requested_at"])
    if boot is not None and boot > requested - timedelta(seconds=5):
        entry.update({"state": "verified", "booted_at": boot.isoformat()})
        core.add_event(db, "ACS_REBOOT_VERIFIED", customer_id=device.customer_id, device_id=device.id, source="worker",
                       details={"booted_at": boot.isoformat()})
    elif now - requested > REBOOT_GIVE_UP:
        entry["state"] = "unconfirmed"
    else:
        return entry
    data["acs_reboot"] = entry
    device.inventory_data = data
    return entry


@router.get("/devices/{device_id}/acs/wifi")
def device_acs_wifi(request: Request, device_id: uuid.UUID, refresh: int = 0):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device, connection = _linked(db, device_id)
        can_write = core.has_permission(user, "devices.write")
        try:
            if refresh and can_write:
                record = nbi._read(connection, device.external_device_id, ["Device.WiFi", "InternetGatewayDevice.LANDevice"])
                hint = read_wifi(record)["model"]
                nbi._task(connection, device.external_device_id, {"name": "refreshObject", "objectName": TR181_WIFI if hint == "tr181" else TR098_WLAN})
            record = nbi._read(connection, device.external_device_id, ["Device.WiFi", "InternetGatewayDevice.LANDevice.1.WLANConfiguration"])
        except nbi.AcsDiagnosticError as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        return {**read_wifi(record), "can_write": can_write}


@router.post("/devices/{device_id}/acs/wifi", dependencies=[Depends(require_csrf)])
def device_acs_wifi_change(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device, connection = _linked(db, device_id)
        try:
            current = read_wifi(nbi._read(connection, device.external_device_id, ["Device.WiFi", "InternetGatewayDevice.LANDevice.1.WLANConfiguration"]))
            values, fields = build_changes(current, (body or {}).get("networks") or [], (body or {}).get("radios") or [])
            started = nbi._task(connection, device.external_device_id, {"name": "setParameterValues", "parameterValues": values})
        except nbi.AcsDiagnosticError as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        core.add_event(db, "ACS_WIFI_CHANGED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                       details={"changes": fields, "started": started})
        db.commit()
        return {"applied": started, "changes": fields}


@router.get("/devices/{device_id}/acs/reboot")
def device_acs_reboot_state(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        ui_user(request, db, "devices.read")
        device, connection = _linked(db, device_id)
        try:
            entry = reboot_state(db, device, connection)
        except nbi.AcsDiagnosticError as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        db.commit()
        return {"reboot": entry}


@router.post("/devices/{device_id}/acs/reboot", dependencies=[Depends(require_csrf)])
def device_acs_reboot(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device, connection = _linked(db, device_id)
        name = device.display_name or device.device_identity or device.name
        if str((body or {}).get("confirm") or "").strip() != name:
            raise HTTPException(422, {"code": "confirm", "message": "Scrivi il nome dell'apparato per confermare il riavvio."})
        try:
            started = nbi._task(connection, device.external_device_id, {"name": "reboot"})
        except nbi.AcsDiagnosticError as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        data = dict(device.inventory_data or {})
        data["acs_reboot"] = {"state": "requested", "requested_at": utcnow().isoformat(), "by": user.username, "started": started}
        device.inventory_data = data
        core.add_event(db, "ACS_REBOOT_REQUESTED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                       details={"started": started})
        db.commit()
        return {"reboot": data["acs_reboot"]}


def install_acs_actions(app) -> None:
    app.include_router(router)
