"""Remote syslog of TR-069 CPEs, set through GenieACS towards the NSM receiver.

TP-Link CPEs (Aginet firmware, e.g. VX230v) keep their log settings in the vendor object
``Device.DeviceInfo.X_TP_LogCfg``: ``LogToRemote``, ``ServerIP``, ``ServerPort``, ``RemoteSeverity``
(syslog levels 0–7). NSM writes those four values and nothing else — local logging and facility stay
as they are. A TP-Link line carries no NSM key: the receiver attributes it by sender address, i.e. the
CPE's management IP, which NSM re-reads from GenieACS (connection-request URL, the WAN address) right
before configuring.

- one CPE from its Syslog tab (connection request: applied at once when the CPE answers);
- every associated CPE from *Amministrazione → ACS* (queued: applied at the next Inform);
- the worker does the same for newly associated CPEs when *Configura automaticamente* is on in the
  syslog settings (never twice for the same target and level).
"""
from __future__ import annotations

import ipaddress
import json
import os
import uuid
import socket
from datetime import datetime
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app import acs_profiles as profiles
from app import acs_diagnostics
from app import genieacs_connector as acs
from app import main as core
from app import syslog_receiver
from app.db import SessionLocal
from app.models import Device, utcnow
from app.ui_api import require_csrf, ui_user

ROOT = profiles.vendor_path("tp-link", "syslog")
PROBE = f"{ROOT}.ServerIP"
LEVELS = {"error": 3, "warning": 4, "notice": 5, "info": 6, "debug": 7}
DEFAULT_LEVEL = "info"
PORT = 514
AUTO_BATCH = 50
router = APIRouter(prefix="/api/ui/v1", tags=["ui"])


class AcsSyslogError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _ipv4(host: str) -> str | None:
    host = (host or "").strip()
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.gethostbyname(host))
        except (OSError, ValueError):
            return None
    if address.version != 4 or address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast:
        return None
    return str(address)


def target(db, request=None) -> str | None:
    """The IPv4 the CPEs send syslog to: the receiver's public address, else the ACS address the CPEs use
    (``ACS_CWMP_URL``), else the host the administrator opened NSM with."""
    configured = _ipv4(str(syslog_receiver.load_settings(db).get("public_host") or ""))
    if configured:
        return configured
    acs_url = os.getenv("ACS_CWMP_URL", "").strip()
    if acs_url:
        from_acs = _ipv4(urlsplit(acs_url if "://" in acs_url else f"http://{acs_url}").hostname or "")
        if from_acs:
            return from_acs
    if request is not None:
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        return _ipv4(urlsplit(f"//{host}").hostname or "")
    return None


def eligible(device: Device) -> bool:
    return device.management_source == "tr069" and bool(device.external_device_id) and device.vendor == "tp-link"


def state_of(device: Device) -> dict | None:
    return ((device.inventory_data or {}).get("tr069") or {}).get("syslog")


def _values(server_ip: str, severity: int) -> list:
    return [[f"{ROOT}.LogToRemote", True, "xsd:boolean"], [f"{ROOT}.ServerIP", server_ip, "xsd:string"],
            [f"{ROOT}.ServerPort", PORT, "xsd:unsignedInt"], [f"{ROOT}.RemoteSeverity", severity, "xsd:unsignedInt"]]


def _supported(connection, external_id: str) -> bool:
    doc = acs_diagnostics._read(connection, external_id, [PROBE])
    node = doc
    for part in PROBE.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return isinstance(node, dict)


def _queue(connection, external_id: str, task: dict) -> None:
    with acs_diagnostics._client(connection) as client:
        response = client.post(f"/devices/{quote(external_id, safe='')}/tasks", content=json.dumps(task),
                               headers={"Content-Type": "application/json"})
    if response.status_code == 404:
        raise acs_diagnostics.AcsDiagnosticError("Il CPE non è più presente in GenieACS.")
    if response.status_code not in (200, 202):
        raise acs_diagnostics.AcsDiagnosticError(f"GenieACS ha rifiutato il comando (HTTP {response.status_code}).")


def configure(db, user, device: Device, server_ip: str, level: str = DEFAULT_LEVEL, immediate: bool = True, source: str = "portal") -> dict:
    """Write the CPE's remote syslog towards ``server_ip`` (audited). Caller commits."""
    if not eligible(device):
        raise AcsSyslogError("not_tr069", "Disponibile per i CPE TP-Link associati a GenieACS.")
    if level not in LEVELS:
        raise AcsSyslogError("level", "Livello non valido.", 422)
    connection = acs._connection(db)
    if connection is None or not connection.is_enabled:
        raise AcsSyslogError("acs", "GenieACS non è configurato o è disabilitato.")
    try:
        # The receiver attributes TP-Link lines by sender address: keep the management IP current first.
        candidate = acs.lookup_by_external_id(connection, device.external_device_id)
        if candidate.get("management_ip") and candidate["management_ip"] != device.management_ip:
            previous = device.management_ip
            device.management_ip = candidate["management_ip"]
            core.add_event(db, "GENIEACS_INVENTORY_REFRESHED", actor=user, customer_id=device.customer_id, device_id=device.id, source=source,
                           details={"changes": {"management_ip": {"before": previous, "after": device.management_ip}}})
        if not _supported(connection, device.external_device_id):
            raise AcsSyslogError("unsupported", "Questo firmware non espone le impostazioni di log via TR-069 (X_TP_LogCfg).")
        task = {"name": "setParameterValues", "parameterValues": _values(server_ip, LEVELS[level])}
        if immediate:
            applied = acs_diagnostics._task(connection, device.external_device_id, task)
        else:
            _queue(connection, device.external_device_id, task)
            applied = False
    except (acs.GenieAcsConnectorError, acs_diagnostics.AcsDiagnosticError) as exc:
        raise AcsSyslogError("acs", str(exc)) from exc
    state = {"server_ip": server_ip, "port": PORT, "level": level, "applied": bool(applied), "at": utcnow().isoformat()}
    data = dict(device.inventory_data or {})
    tr = dict(data.get("tr069") or {})
    tr["syslog"] = state
    data["tr069"] = tr
    device.inventory_data = data
    core.add_event(db, "ACS_SYSLOG_CONFIGURED", actor=user, customer_id=device.customer_id, device_id=device.id, source=source,
                   details={"server_ip": server_ip, "port": PORT, "level": level, "applied": bool(applied)})
    return state


def candidates(db) -> list[Device]:
    return [d for d in db.scalars(select(Device).where(Device.management_source == "tr069", Device.external_device_id.is_not(None))) if eligible(d)]


def configure_all(db, user, server_ip: str, level: str = DEFAULT_LEVEL) -> dict:
    stats = {"queued": 0, "skipped": 0, "failed": 0, "errors": []}
    for device in candidates(db):
        current = state_of(device) or {}
        if current.get("server_ip") == server_ip and current.get("level") == level:
            stats["skipped"] += 1
            continue
        try:
            configure(db, user, device, server_ip, level, immediate=False)
            stats["queued"] += 1
        except AcsSyslogError as exc:
            stats["failed"] += 1
            if len(stats["errors"]) < 5:
                stats["errors"].append({"device": device.display_name or device.name, "message": exc.message})
    return stats


def auto_configure(now: datetime | None = None) -> dict:
    """Worker task: with syslog auto-configuration on, set syslog on CPEs that do not have this target yet."""
    stats = {"queued": 0, "failed": 0}
    with SessionLocal() as db:
        if not syslog_receiver.load_settings(db).get("auto_configure"):
            return stats
        server_ip = target(db)
        connection = acs._connection(db)
        if not server_ip or connection is None or not connection.is_enabled:
            return stats
        for device in candidates(db):
            if stats["queued"] + stats["failed"] >= AUTO_BATCH:
                break
            current = state_of(device) or {}
            if current.get("server_ip") == server_ip or current.get("failed_target") == server_ip:
                continue
            try:
                configure(db, None, device, server_ip, current.get("level") or DEFAULT_LEVEL, immediate=False, source="worker")
                stats["queued"] += 1
            except AcsSyslogError:
                # Not supported or GenieACS refused: remember it for this target, the operator can retry by hand.
                data = dict(device.inventory_data or {})
                tr = dict(data.get("tr069") or {})
                tr["syslog"] = {**current, "failed_target": server_ip, "at": utcnow().isoformat()}
                data["tr069"] = tr
                device.inventory_data = data
                stats["failed"] += 1
        db.commit()
    return stats


# --- API ---------------------------------------------------------------------------------------------

def _fail(exc: AcsSyslogError):
    raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from None


@router.get("/devices/{device_id}/syslog/acs")
def device_state(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "monitoring.read")
        device = db.get(Device, device_id)
        if device is None:
            raise HTTPException(404)
        return {"eligible": eligible(device), "state": state_of(device), "target": target(db, request), "port": PORT,
                "levels": list(LEVELS), "default_level": DEFAULT_LEVEL, "management_ip": device.management_ip,
                "can_configure": eligible(device) and core.has_permission(user, "devices.write")}


@router.post("/devices/{device_id}/syslog/acs", dependencies=[Depends(require_csrf)])
async def device_configure(request: Request, device_id: uuid.UUID):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = db.get(Device, device_id)
        if device is None:
            raise HTTPException(404)
        server_ip = target(db, request)
        if not server_ip:
            _fail(AcsSyslogError("no_target", "Indirizzo IPv4 di NSM non determinabile: impostalo in Amministrazione → Syslog."))
        try:
            state = configure(db, user, device, server_ip, str(body.get("level") or DEFAULT_LEVEL))
        except AcsSyslogError as exc:
            db.rollback()
            _fail(exc)
        db.commit()
        return {"state": state, "management_ip": device.management_ip}


@router.get("/admin/acs/syslog")
def admin_state(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        server_ip = target(db, request)
        devices = candidates(db)
        configured = sum(1 for d in devices if (state_of(d) or {}).get("server_ip") == server_ip)
        return {"target": server_ip, "port": PORT, "levels": list(LEVELS), "default_level": DEFAULT_LEVEL,
                "cpes": len(devices), "configured": configured,
                "auto": bool(syslog_receiver.load_settings(db).get("auto_configure"))}


@router.post("/admin/acs/syslog", dependencies=[Depends(require_csrf)])
async def admin_configure_all(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        server_ip = target(db, request)
        if not server_ip:
            _fail(AcsSyslogError("no_target", "Indirizzo IPv4 di NSM non determinabile: impostalo in Amministrazione → Syslog."))
        level = str(body.get("level") or DEFAULT_LEVEL)
        if level not in LEVELS:
            _fail(AcsSyslogError("level", "Livello non valido.", 422))
        stats = configure_all(db, user, server_ip, level)
        db.commit()
        return stats


def install_acs_syslog(app) -> None:
    app.include_router(router)
