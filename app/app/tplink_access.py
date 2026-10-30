"""ACS-09: remote web access and LAN Telnet of TR-069 CPEs, from the device's *ACS* tab (operator decisions 2026-10-23).

- **Remote web access** (TR-181 ``Device.UserInterface.RemoteAccess``: ``Enable``, ``Protocol``, ``Port``; TP-Link
  ``X_TP_RemoteAll`` and ``X_TP_RemoteHost``): shown on the device page with a warning when it is reachable from any
  address, or with the addresses it is limited to; switched on or off, moved to another port or protocol, limited to
  some addresses or opened to all, by technicians. The allow-list is what the CPE's own page *Client Device Allowed for
  Remote Management → Only the Following IP Addresses* writes, checked on a VX230v (0.19.0) on 2026-10-24:
  ``X_TP_RemoteAll`` false and ``X_TP_RemoteHost`` a comma-separated list of IPv4 networks in CIDR notation
  (``192.0.2.10/32,198.51.100.0/24``). A single address without ``/prefix`` is not accepted by that firmware (the
  earlier attempt that closed the page for everyone), so NSM always writes ``address/prefix``. Limiting needs the device
  name typed as confirmation (a wrong list locks the technicians out; recovery: open to all). Nothing changes by
  default (operator decision 2026-10-23): open pages are only reported. Every change of port, protocol or allow-list
  is applied with ``Enable`` false then true, as the CPE only re-reads its settings then (seen on the VX230v).
- **Remote HTTP page of older firmware** (TP-Link ``Device.X_TP_AppCfg.HttpCfg.HttpRemoteEnabled``, seen on an Archer
  VR1210v, 2.0.0 0.10 Build 260323): a second, unencrypted page that ``UserInterface.RemoteAccess`` does not report.
  Shown with a warning when on, switched by technicians. Its ``RemoteHost`` is not used: nobody has checked the format
  that firmware accepts, and a wrong value can lock the technicians out.
- **LAN Telnet** (TP-Link ``Device.X_TP_AppCfg.TelnetCfg.TelnetLocalEnabled``): off by default. Once a day per
  TP-Link CPE the worker (``enforce_telnet_policy``) queues ``TelnetLocalEnabled`` false for the CPE's next contact
  when GenieACS shows it on, unless a technician allowed it on that device (``inventory_data["tplink_access"]``).
  Audited ``ACS_TELNET_DISABLED``.

- **ISP default** (TP-Link *Aginet Config → Save current settings as default settings*; TR-069
  ``Device.X_TP_ISPRestore.ISPConfigFlag``): true when the CPE has an ISP default, the configuration it returns to after
  a reset instead of TP-Link's factory values. Checked on the operator's VX230v (0.19.0) on 2026-10-24: writing the flag
  false then true over TR-069 saves the running configuration as the new default (a marker survived a factory reset);
  writing true when it is already true does nothing; false alone leaves the running configuration untouched. *Salva come
  default* (``devices.write``, device name typed) sends the two writes and is audited ``ACS_ISP_DEFAULT_SAVED``; the tab
  says when NSM changed the CPE after the last save, so the operator knows the default is older.

``GET /api/ui/v1/devices/{id}/acs/access`` (``devices.read``) reads GenieACS only; ``POST`` (``devices.write``)
applies one change with a connection request, audited ``ACS_ACCESS_CHANGED``.
"""
from __future__ import annotations

import ipaddress
import json
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import acs_diagnostics as nbi
from app import genieacs_connector as acs
from app import main as core
from app.db import SessionLocal
from app.models import Device, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
REMOTE = "Device.UserInterface.RemoteAccess"
HTTP_CFG = "Device.X_TP_AppCfg.HttpCfg"
HTTP_REMOTE = f"{HTTP_CFG}.HttpRemoteEnabled"
TELNET = "Device.X_TP_AppCfg.TelnetCfg"
TELNET_LAN = f"{TELNET}.TelnetLocalEnabled"
ISP_RESTORE = "Device.X_TP_ISPRestore"
ISP_FLAG = f"{ISP_RESTORE}.ISPConfigFlag"
POLICY_EVERY = timedelta(hours=24)
POLICY_BATCH = 50
PROTOCOLS = ("HTTP", "HTTPS")
MAX_ALLOWED = 16
MAX_HOST_LENGTH = 256


def allow_list(value) -> list[str]:
    """``X_TP_RemoteHost`` as a list of networks (empty entries dropped)."""
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def normalize_allowed(entries) -> list[str]:
    """Operator input → IPv4 networks as the CPE stores them (``address/prefix``; a bare address becomes ``/32``)."""
    if not isinstance(entries, list) or not entries:
        raise HTTPException(422, {"code": "allowed", "message": "Indica almeno un indirizzo o una rete."})
    out: list[str] = []
    for entry in entries:
        text = str(entry or "").strip()
        try:
            network = ipaddress.ip_network(text if "/" in text else f"{text}/32", strict=False)
        except ValueError:
            raise HTTPException(422, {"code": "allowed", "message": f"Indirizzo o rete non valida: {text[:60] or '(vuoto)'}."}) from None
        if network.version != 4:
            raise HTTPException(422, {"code": "allowed", "message": "Il CPE accetta solo indirizzi IPv4."})
        value = f"{network.network_address}/{network.prefixlen}"
        if value not in out:
            out.append(value)
    if len(out) > MAX_ALLOWED or len(",".join(out)) > MAX_HOST_LENGTH:
        raise HTTPException(422, {"code": "allowed", "message": f"Al massimo {MAX_ALLOWED} indirizzi o reti."})
    return out


def _confirm_name(device) -> str:
    return device.display_name or device.device_identity or device.name


def _read(connection, external_id: str) -> dict:
    return nbi._read(connection, external_id, ["_lastInform", REMOTE, HTTP_CFG, TELNET, ISP_RESTORE])


def _bool(value):
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    return str(value).strip().lower() in ("1", "true")


def state(record: dict) -> dict:
    """Remote access and LAN Telnet as GenieACS last read them (None where the CPE does not expose them)."""
    remote = None
    if isinstance(nbi._node(record, REMOTE), dict) and nbi._value(record, f"{REMOTE}.Enable") is not None:
        supported = str(nbi._value(record, f"{REMOTE}.SupportedProtocols") or "")
        remote = {"enabled": bool(_bool(nbi._value(record, f"{REMOTE}.Enable"))),
                  "protocol": str(nbi._value(record, f"{REMOTE}.Protocol") or "") or None,
                  "port": nbi._number(nbi._value(record, f"{REMOTE}.Port")),
                  "open_to_all": _bool(nbi._value(record, f"{REMOTE}.X_TP_RemoteAll")),
                  "protocols": [p for p in PROTOCOLS if p in supported.split(",")] or list(PROTOCOLS)}
        remote["allowed"] = allow_list(nbi._value(record, f"{REMOTE}.X_TP_RemoteHost")) if remote["open_to_all"] is False else []
        # The allow-list can be edited only where the CPE exposes both TP-Link parameters.
        remote["allow_list_supported"] = remote["open_to_all"] is not None and nbi._value(record, f"{REMOTE}.X_TP_RemoteHost") is not None
        # Without X_TP_RemoteAll the CPE does not say who may connect: treat an enabled page as reachable by anyone.
        remote["exposed"] = remote["enabled"] and remote["open_to_all"] is not False
    telnet = _bool(nbi._value(record, TELNET_LAN))
    flag = _bool(nbi._value(record, ISP_FLAG))
    http = _bool(nbi._value(record, HTTP_REMOTE))
    return {"remote": remote, "http_remote": None if http is None else {"enabled": http, "port": nbi._number(nbi._value(record, f"{HTTP_CFG}.HttpRemotePort"))},
            "telnet_lan": None if telnet is None else {"enabled": telnet},
            "isp_default": None if flag is None else {"saved": flag}}


def _settings(device) -> dict:
    return dict((device.inventory_data or {}).get("tplink_access") or {})


def _save_settings(device, values: dict) -> None:
    data = dict(device.inventory_data or {})
    data["tplink_access"] = {**_settings(device), **values}
    device.inventory_data = data


def _payload(device, connection, can_edit: bool) -> dict:
    out = {"remote": None, "http_remote": None, "telnet_lan": None, "telnet_allowed": bool(_settings(device).get("telnet_lan_allowed")),
           "last_inform": None, "can_edit": can_edit, "error": None, "confirm_name": _confirm_name(device)}
    try:
        record = _read(connection, device.external_device_id)
    except nbi.AcsDiagnosticError as exc:
        out["error"] = str(exc)
        return out
    out.update(state(record), last_inform=record.get("_lastInform"))
    if out.get("isp_default") is not None:
        settings = _settings(device)
        saved_at, changed_at = settings.get("default_saved_at"), settings.get("changed_at")
        out["isp_default"].update({"saved_at": saved_at, "saved_by": settings.get("default_saved_by"),
                                   # NSM changed the CPE after its last save: a reset would bring back the older default.
                                   "changed_since": bool(changed_at and (not saved_at or changed_at > saved_at))})
    return out


def _spv(connection, external_id: str, values: list) -> bool:
    return nbi._task(connection, external_id, {"name": "setParameterValues", "parameterValues": values})


def apply_change(db, user, device, connection, body: dict) -> dict:
    """One change from the device page; returns the audit details. Caller commits."""
    record = _read(connection, device.external_device_id)
    current = state(record)
    details: dict = {}
    if body.get("save_default"):
        if current["isp_default"] is None:
            raise HTTPException(422, {"code": "unsupported", "message": "Questo CPE non espone il default ISP (Aginet Config)."})
        if str(body.get("confirm") or "").strip() != _confirm_name(device):
            raise HTTPException(422, {"code": "confirm", "message": "Scrivi il nome dell'apparato per confermare."})
        # False then true: the CPE stores its running configuration as the ISP default only on that transition.
        _spv(connection, device.external_device_id, [[ISP_FLAG, False, "xsd:boolean"]])
        started = _spv(connection, device.external_device_id, [[ISP_FLAG, True, "xsd:boolean"]])
        now = utcnow().isoformat()
        _save_settings(device, {"default_saved_at": now, "default_saved_by": user.username if user else None})
        details = {"started": started, "had_default": current["isp_default"]["saved"]}
        core.add_event(db, "ACS_ISP_DEFAULT_SAVED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal", details=details)
        return details
    if "telnet_lan" in body:
        if current["telnet_lan"] is None:
            raise HTTPException(422, {"code": "unsupported", "message": "Questo CPE non espone il Telnet LAN."})
        enabled = bool(body["telnet_lan"])
        details["started"] = _spv(connection, device.external_device_id, [[TELNET_LAN, enabled, "xsd:boolean"]])
        # Allowed by hand: the daily policy leaves it on; switched off: the policy keeps it off.
        _save_settings(device, {"telnet_lan_allowed": enabled})
        details["telnet_lan"] = enabled
    elif "http_remote" in body:
        if current["http_remote"] is None:
            raise HTTPException(422, {"code": "unsupported", "message": "Questo CPE non espone la pagina web HTTP remota."})
        enabled = bool(body["http_remote"])
        details.update({"started": _spv(connection, device.external_device_id, [[HTTP_REMOTE, enabled, "xsd:boolean"]]), "http_remote": enabled})
    else:
        remote = current["remote"]
        if remote is None:
            raise HTTPException(422, {"code": "unsupported", "message": "Questo CPE non espone l'accesso web remoto."})
        values = []
        if "allowed" in body or body.get("allow_all"):
            if not remote.get("allow_list_supported"):
                raise HTTPException(422, {"code": "unsupported", "message": "Questo CPE non espone la lista degli indirizzi ammessi."})
            before = remote.get("allowed") if remote.get("open_to_all") is False else "tutti"
            if body.get("allow_all"):
                values = [[f"{REMOTE}.X_TP_RemoteAll", True, "xsd:boolean"], [f"{REMOTE}.X_TP_RemoteHost", "", "xsd:string"]]
                after = "tutti"
            else:
                if str(body.get("confirm") or "").strip() != _confirm_name(device):
                    raise HTTPException(422, {"code": "confirm", "message": "Scrivi il nome dell'apparato per confermare la limitazione."})
                allowed = normalize_allowed(body.get("allowed"))
                values = [[f"{REMOTE}.X_TP_RemoteHost", ",".join(allowed), "xsd:string"], [f"{REMOTE}.X_TP_RemoteAll", False, "xsd:boolean"]]
                after = allowed
            _spv(connection, device.external_device_id, values)
            if remote["enabled"]:
                # Like port and protocol: the CPE applies the allow-list only when the page is switched off and on again.
                _spv(connection, device.external_device_id, [[f"{REMOTE}.Enable", False, "xsd:boolean"]])
            details.update({"started": _spv(connection, device.external_device_id, [[f"{REMOTE}.Enable", remote["enabled"], "xsd:boolean"]]),
                            "allowed_before": before, "allowed_after": after})
            _save_settings(device, {"changed_at": utcnow().isoformat()})
            core.add_event(db, "ACS_ACCESS_CHANGED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal", details=details)
            return details
        protocol = body.get("protocol")
        if protocol is not None:
            protocol = str(protocol).upper()
            if protocol not in remote["protocols"]:
                raise HTTPException(422, {"code": "protocol", "message": "Protocollo non supportato dal CPE."})
            values.append([f"{REMOTE}.Protocol", protocol, "xsd:string"])
        if body.get("port") is not None:
            try:
                port = int(body["port"])
            except (TypeError, ValueError):
                port = 0
            if not 1024 <= port <= 65535:
                raise HTTPException(422, {"code": "port", "message": "Usa una porta tra 1024 e 65535."})
            values.append([f"{REMOTE}.Port", port, "xsd:unsignedInt"])
        enabled = bool(body["enabled"]) if "enabled" in body else remote["enabled"]
        if not values and enabled == remote["enabled"]:
            raise HTTPException(422, {"code": "no_change", "message": "Nessuna modifica da applicare."})
        if values:
            _spv(connection, device.external_device_id, values)
        if values and enabled and remote["enabled"]:
            # The CPE applies a new port or protocol only when the page is switched off and on again.
            _spv(connection, device.external_device_id, [[f"{REMOTE}.Enable", False, "xsd:boolean"]])
        details["started"] = _spv(connection, device.external_device_id, [[f"{REMOTE}.Enable", enabled, "xsd:boolean"]])
        details.update({"remote_enabled": enabled, **{v[0].rsplit(".", 1)[1].lower(): v[1] for v in values}})
    _save_settings(device, {"changed_at": utcnow().isoformat()})
    core.add_event(db, "ACS_ACCESS_CHANGED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal", details=details)
    return details


def _queue(connection, external_id: str, values: list) -> None:
    """``setParameterValues`` without a connection request: applied at the CPE's next periodic contact."""
    try:
        with nbi._client(connection) as client:
            response = client.post(f"/devices/{quote(external_id, safe='')}/tasks",
                                   content=json.dumps({"name": "setParameterValues", "parameterValues": values}),
                                   headers={"Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        raise nbi.AcsDiagnosticError("GenieACS non raggiungibile.") from exc
    if response.status_code not in (200, 202):
        raise nbi.AcsDiagnosticError(f"GenieACS ha rifiutato il comando (HTTP {response.status_code}).")


def enforce_telnet_policy(now: datetime | None = None) -> dict:
    """Worker task: once a day per TP-Link CPE, switch LAN Telnet off when GenieACS shows it on and nobody allowed it."""
    now = now or utcnow()
    switched = 0
    with SessionLocal() as db:
        connection = acs._connection(db)
        if connection is None or not connection.is_enabled:
            return {"switched": 0}
        devices = db.scalars(select(Device).where(Device.external_device_id.is_not(None), Device.vendor == "tp-link"))
        checked = 0
        for device in devices:
            settings = _settings(device)
            try:
                due = not settings.get("checked_at") or now - datetime.fromisoformat(settings["checked_at"]) >= POLICY_EVERY
            except (TypeError, ValueError):
                due = True
            if not due or settings.get("telnet_lan_allowed"):
                continue
            try:
                telnet = state(_read(connection, device.external_device_id))["telnet_lan"]
                if telnet and telnet["enabled"]:
                    _queue(connection, device.external_device_id, [[TELNET_LAN, False, "xsd:boolean"]])
                    switched += 1
                    core.add_event(db, "ACS_TELNET_DISABLED", customer_id=device.customer_id, device_id=device.id, source="worker",
                                   details={"policy": "telnet_lan_off"})
            except nbi.AcsDiagnosticError:
                continue  # retried at the next cycle
            _save_settings(device, {"checked_at": now.isoformat()})
            checked += 1
            if checked >= POLICY_BATCH:
                break
        db.commit()
    return {"switched": switched}


@router.get("/devices/{device_id}/acs/access")
def device_access(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device, connection = nbi._linked(db, device_id)
        return _payload(device, connection, core.has_permission(user, "devices.write"))


@router.post("/devices/{device_id}/acs/access", dependencies=[Depends(require_csrf)])
def device_access_change(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device, connection = nbi._linked(db, device_id)
        try:
            apply_change(db, user, device, connection, body or {})
        except nbi.AcsDiagnosticError as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        db.commit()
        return _payload(device, connection, True)


def install_tplink_access(app) -> None:
    app.include_router(router)
