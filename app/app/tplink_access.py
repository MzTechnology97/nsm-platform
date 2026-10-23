"""ACS-09: remote web access and LAN Telnet of TR-069 CPEs, from the device's *ACS* tab (operator decisions 2026-10-23).

- **Remote web access** (TR-181 ``Device.UserInterface.RemoteAccess``: ``Enable``, ``Protocol``, ``Port``; TP-Link
  ``X_TP_RemoteAll``): shown on the device page with a warning when it is reachable from any address, and switched on or
  off, or moved to another port or protocol, by technicians. Restricting it to some addresses is not offered: on a
  VX230v (0.19.0) ``X_TP_RemoteAll`` false closed the page for every address, the allowed host included, and the
  ``X_TP_AppCfg.ACL`` entries refused every value (cwmp 9003). Nothing changes by default: open pages are only
  reported. A change of port or protocol is applied with ``Enable`` false then true, as the CPE only re-reads its
  settings then (seen on the VX230v).
- **LAN Telnet** (TP-Link ``Device.X_TP_AppCfg.TelnetCfg.TelnetLocalEnabled``): off by default. Once a day per
  TP-Link CPE the worker (``enforce_telnet_policy``) queues ``TelnetLocalEnabled`` false for the CPE's next contact
  when GenieACS shows it on, unless a technician allowed it on that device (``inventory_data["tplink_access"]``).
  Audited ``ACS_TELNET_DISABLED``.

``GET /api/ui/v1/devices/{id}/acs/access`` (``devices.read``) reads GenieACS only; ``POST`` (``devices.write``)
applies one change with a connection request, audited ``ACS_ACCESS_CHANGED``.
"""
from __future__ import annotations

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
TELNET = "Device.X_TP_AppCfg.TelnetCfg"
TELNET_LAN = f"{TELNET}.TelnetLocalEnabled"
POLICY_EVERY = timedelta(hours=24)
POLICY_BATCH = 50
PROTOCOLS = ("HTTP", "HTTPS")


def _read(connection, external_id: str) -> dict:
    return nbi._read(connection, external_id, ["_lastInform", REMOTE, TELNET])


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
        # Without X_TP_RemoteAll the CPE does not say who may connect: treat an enabled page as reachable by anyone.
        remote["exposed"] = remote["enabled"] and remote["open_to_all"] is not False
    telnet = _bool(nbi._value(record, TELNET_LAN))
    return {"remote": remote, "telnet_lan": None if telnet is None else {"enabled": telnet}}


def _settings(device) -> dict:
    return dict((device.inventory_data or {}).get("tplink_access") or {})


def _save_settings(device, values: dict) -> None:
    data = dict(device.inventory_data or {})
    data["tplink_access"] = {**_settings(device), **values}
    device.inventory_data = data


def _payload(device, connection, can_edit: bool) -> dict:
    out = {"remote": None, "telnet_lan": None, "telnet_allowed": bool(_settings(device).get("telnet_lan_allowed")),
           "last_inform": None, "can_edit": can_edit, "error": None}
    try:
        record = _read(connection, device.external_device_id)
    except nbi.AcsDiagnosticError as exc:
        out["error"] = str(exc)
        return out
    out.update(state(record), last_inform=record.get("_lastInform"))
    return out


def _spv(connection, external_id: str, values: list) -> bool:
    return nbi._task(connection, external_id, {"name": "setParameterValues", "parameterValues": values})


def apply_change(db, user, device, connection, body: dict) -> dict:
    """One change from the device page; returns the audit details. Caller commits."""
    record = _read(connection, device.external_device_id)
    current = state(record)
    details: dict = {}
    if "telnet_lan" in body:
        if current["telnet_lan"] is None:
            raise HTTPException(422, {"code": "unsupported", "message": "Questo CPE non espone il Telnet LAN."})
        enabled = bool(body["telnet_lan"])
        details["started"] = _spv(connection, device.external_device_id, [[TELNET_LAN, enabled, "xsd:boolean"]])
        # Allowed by hand: the daily policy leaves it on; switched off: the policy keeps it off.
        _save_settings(device, {"telnet_lan_allowed": enabled})
        details["telnet_lan"] = enabled
    else:
        remote = current["remote"]
        if remote is None:
            raise HTTPException(422, {"code": "unsupported", "message": "Questo CPE non espone l'accesso web remoto."})
        values = []
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
