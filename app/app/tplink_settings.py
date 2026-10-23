"""ACS-10: advanced settings of TP-Link CPEs over TR-069, not all offered by the CPE's own web page (operator request
2026-10-23). Values as read on a VX230v and an HB210; only parameters whose meaning is clear are offered:

- **Upgrades**: ``ManagementServer.UpgradesManaged`` (true: firmware only from the ACS) and TP-Link's automatic upgrade
  from its cloud (``X_TP_CloudService.FwAutoUpgrade.Enable``; on by default on the HB210 seen).
- **Network**: NTP servers 1–2 (``Device.Time``), NAT session timeouts (``X_TP_SessionTimeout``) and the time of the
  scheduled reboot (``X_TP_AppCfg.RebootScheduleCfg``: ``Enable``, ``Hours``, ``Minutes``; the CPE's recurrence is kept).
- **Customer pages**: whether the customer may change each page of the CPE's web interface
  (``X_TP_USER_RESTRICTION.PageAccessRights.<page>.Editable``). ``VisibilityLevel`` is not offered: its values are
  not documented.
- **Administrator password** (``X_TP_UserCfg.AdminPwd``): write-only, never stored or shown by NSM, typed confirmation.

``GET /api/ui/v1/devices/{id}/acs/settings`` (``devices.read``) reads GenieACS only; ``POST .../refresh`` re-reads the
objects from the CPE; ``POST`` (``devices.write``) sends the changed values in one ``setParameterValues`` with a
connection request, audited ``ACS_SETTINGS_CHANGED`` (names only, never the password).
"""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import acs_diagnostics as nbi
from app import main as core
from app.db import SessionLocal
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
PAGES = "Device.X_TP_USER_RESTRICTION.PageAccessRights"
ADMIN_PASSWORD = "Device.X_TP_UserCfg.AdminPwd"
OBJECTS = ["Device.ManagementServer.UpgradesManaged", "Device.X_TP_CloudService.FwAutoUpgrade", "Device.Time", "Device.X_TP_SessionTimeout",
           "Device.X_TP_AppCfg.RebootScheduleCfg", PAGES, "Device.X_TP_UserCfg"]
HOST = re.compile(r"^[A-Za-z0-9.:-]{1,64}$")

# key → (group, path, kind, minimum, maximum)
FIELDS = {
    "upgrades_managed": ("upgrades", "Device.ManagementServer.UpgradesManaged", "bool", None, None),
    "cloud_auto_upgrade": ("upgrades", "Device.X_TP_CloudService.FwAutoUpgrade.Enable", "bool", None, None),
    "ntp_server_1": ("network", "Device.Time.NTPServer1", "host", None, None),
    "ntp_server_2": ("network", "Device.Time.NTPServer2", "host", None, None),
    "nat_timeouts": ("network", "Device.X_TP_SessionTimeout.Enable", "bool", None, None),
    "nat_tcp_established": ("network", "Device.X_TP_SessionTimeout.TcpTimeoutEstablished", "uint", 60, 864000),
    "nat_udp": ("network", "Device.X_TP_SessionTimeout.UdpTimeout", "uint", 10, 3600),
    "nat_udp_stream": ("network", "Device.X_TP_SessionTimeout.UdpTimeoutStream", "uint", 10, 3600),
    "reboot_schedule": ("network", "Device.X_TP_AppCfg.RebootScheduleCfg.Enable", "bool", None, None),
    "reboot_hours": ("network", "Device.X_TP_AppCfg.RebootScheduleCfg.Hours", "uint", 0, 23),
    "reboot_minutes": ("network", "Device.X_TP_AppCfg.RebootScheduleCfg.Minutes", "uint", 0, 59),
}
TYPES = {"bool": "xsd:boolean", "uint": "xsd:unsignedInt", "host": "xsd:string"}


def _read(connection, external_id: str) -> dict:
    return nbi._read(connection, external_id, ["_lastInform", *OBJECTS])


def _bool(value):
    if isinstance(value, bool) or value is None:
        return value
    return str(value).strip().lower() in ("1", "true")


def state(record: dict) -> dict:
    """The offered settings the CPE exposes, as GenieACS last read them."""
    fields = {}
    for key, (group, path, kind, low, high) in FIELDS.items():
        node = nbi._node(record, path)
        if not isinstance(node, dict) or "_value" not in node:
            continue
        value = node.get("_value")
        value = _bool(value) if kind == "bool" else nbi._number(value) if kind == "uint" else (str(value) if value is not None else "")
        fields[key] = {"group": group, "kind": kind, "value": value, "min": low, "max": high, "writable": bool(node.get("_writable"))}
    pages = []
    for name, node in sorted((nbi._node(record, PAGES) or {}).items()):
        editable = node.get("Editable") if isinstance(node, dict) and not name.startswith("_") else None
        if isinstance(editable, dict) and "_value" in editable:
            pages.append({"name": name, "editable": _bool(editable.get("_value"))})
    password = nbi._node(record, ADMIN_PASSWORD)
    return {"fields": fields, "pages": pages, "admin_password": isinstance(password, dict) and bool(password.get("_writable"))}


def _payload(device, connection, can_edit: bool) -> dict:
    out = {"fields": {}, "pages": [], "admin_password": False, "last_inform": None, "can_edit": can_edit, "error": None,
           "confirm_name": device.display_name or device.device_identity or device.name}
    try:
        record = _read(connection, device.external_device_id)
    except nbi.AcsDiagnosticError as exc:
        out["error"] = str(exc)
        return out
    out.update(state(record), last_inform=record.get("_lastInform"))
    return out


def _coerce(key: str, value, kind: str, low, high):
    if kind == "bool":
        if not isinstance(value, bool):
            raise HTTPException(422, {"code": "invalid", "message": f"Valore non valido per {key}."})
        return value
    if kind == "uint":
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = None
        if number is None or not low <= number <= high:
            raise HTTPException(422, {"code": "invalid", "message": f"{key}: usa un numero tra {low} e {high}."})
        return number
    text = str(value or "").strip()
    if text and not HOST.match(text):
        raise HTTPException(422, {"code": "invalid", "message": f"{key}: indirizzo o nome host non valido."})
    return text


def apply_changes(db, user, device, connection, body: dict) -> list[str]:
    """Validate and send the changes in one task; returns the names changed (audited). Caller commits."""
    current = state(_read(connection, device.external_device_id))
    values, changed = [], []
    for key, value in (body.get("values") or {}).items():
        field = current["fields"].get(key)
        if key not in FIELDS or field is None or not field["writable"]:
            raise HTTPException(422, {"code": "unsupported", "message": f"Il CPE non espone {key}."})
        group, path, kind, low, high = FIELDS[key]
        value = _coerce(key, value, kind, low, high)
        if value != field["value"]:
            values.append([path, value, TYPES[kind]])
            changed.append(key)
    known = {page["name"]: page["editable"] for page in current["pages"]}
    for name, editable in (body.get("pages") or {}).items():
        if name not in known or not isinstance(editable, bool):
            raise HTTPException(422, {"code": "unsupported", "message": f"Pagina sconosciuta: {name}."})
        if editable != known[name]:
            values.append([f"{PAGES}.{name}.Editable", editable, "xsd:boolean"])
            changed.append(f"page:{name}")
    password = body.get("admin_password")
    if password:
        if not current["admin_password"]:
            raise HTTPException(422, {"code": "unsupported", "message": "Il CPE non espone la password admin."})
        if str(body.get("confirm") or "").strip() != (device.display_name or device.device_identity or device.name):
            raise HTTPException(422, {"code": "confirm", "message": "Scrivi il nome dell'apparato per cambiare la password admin."})
        if len(str(password)) < 8 or len(str(password)) > 64 or any(ord(c) < 33 or ord(c) > 126 for c in str(password)):
            raise HTTPException(422, {"code": "password", "message": "Password admin: 8–64 caratteri stampabili, senza spazi."})
        values.append([ADMIN_PASSWORD, str(password), "xsd:string"])
        changed.append("admin_password")
    if not values:
        raise HTTPException(422, {"code": "no_change", "message": "Nessuna modifica da applicare."})
    started = nbi._task(connection, device.external_device_id, {"name": "setParameterValues", "parameterValues": values})
    core.add_event(db, "ACS_SETTINGS_CHANGED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"changed": changed, "started": started})
    return changed


@router.get("/devices/{device_id}/acs/settings")
def device_settings(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device, connection = nbi._linked(db, device_id)
        return _payload(device, connection, core.has_permission(user, "devices.write"))


@router.post("/devices/{device_id}/acs/settings/refresh", dependencies=[Depends(require_csrf)])
def device_settings_refresh(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        ui_user(request, db, "devices.write")
        device, connection = nbi._linked(db, device_id)
        try:
            for path in OBJECTS:
                nbi._task(connection, device.external_device_id, {"name": "refreshObject", "objectName": path})
        except nbi.AcsDiagnosticError as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        return _payload(device, connection, True)


@router.post("/devices/{device_id}/acs/settings", dependencies=[Depends(require_csrf)])
def device_settings_change(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device, connection = nbi._linked(db, device_id)
        try:
            apply_changes(db, user, device, connection, body or {})
        except nbi.AcsDiagnosticError as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        db.commit()
        return _payload(device, connection, True)


def install_tplink_settings(app) -> None:
    app.include_router(router)
