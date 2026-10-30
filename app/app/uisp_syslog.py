"""Remote syslog of Ubiquiti airOS devices set through UISP (UBNT-09, operator request 2026-10-23).

UISP 3.x reads an airOS device's services (``GET /devices/{id}/services``) but cannot write them
(``PUT`` answers 501 *setServices is not implemented*), so NSM uses the way UISP itself restores a configuration:

1. UISP takes a fresh backup of the device (``POST /devices/{id}/backups``): it is the restore point;
2. NSM changes only ``syslog.remote.status/ip/port`` in its ``system.cfg`` (and, in *key* mode, appends the NSM key to
   ``resolv.host.1.name``, the airOS hostname);
3. the changed file is uploaded as a new backup (``PUT /devices/{id}/backups``) and applied (``.../{backupId}/apply``):
   the CPE reboots and is back in 2–3 minutes (checked on a LiteBeam 5AC, airOS 8.7.15).

Applying a backup rewrites the whole configuration and reboots the CPE, so it runs only when an operator asks for it
from the device page and types the device name; never from the worker. *Ripristina* applies the restore point.

How NSM recognises the lines (per device, ``mode``). airOS 8.7.4 sends remote lines without any hostname (seen on
production, 2026-10-30), so in every mode the receiver also accepts the device's address as UISP reports it, when no
other device uses it (``syslog_identity.build_index``); key and name only help a firmware that does send the hostname:
- ``address`` (default, recommended): the address alone;
- ``key``: also ``NSM-<key>`` in the hostname, like every other device;
- ``customer_name``: also the hostname as the NSM customer's name (words in any order) — accepted only when exactly one
  Ubiquiti device of NSM uses this mode with that name.
"""
from __future__ import annotations

import io
import re
import tarfile
import time
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import main as core
from app import syslog_identity as identity
from app import uisp_connector as uisp
from app import uisp_operations as ops
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import Device, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
KEY = "uisp_syslog"
MODES = ("key", "customer_name", "address")
DEFAULT_MODE = "address"
# airOS has no remote-syslog level (system.cfg: syslog.remote.status/ip/port only) and sends everything: the receiver keeps
# the lines at this severity or worse (logins are still recorded for the Accessi page).
LEVELS = {"error": 3, "warning": 4, "notice": 5, "info": 6, "debug": 7}
DEFAULT_LEVEL = "warning"
PORT = 514
BACKUP_WAIT_SECONDS = 90
HOSTNAME_MAX = 63
SLEEP = time.sleep  # tests replace it
_KEY_SUFFIX = re.compile(r"\s*" + re.escape(identity.KEY_PREFIX) + r"[0-9a-f]{16}\s*")


class UispSyslogError(InputError, ValueError):
    pass


def state_of(device: Device) -> dict | None:
    value = (device.inventory_data or {}).get(KEY)
    return dict(value) if isinstance(value, dict) else None


def device_name(device: Device) -> str:
    return device.display_name or device.device_identity or device.name


def edit_config(text: str, server: str, mode: str, key: str) -> str:
    """``system.cfg`` with remote syslog towards ``server``:514; the line order and every other line stay as they are."""
    lines = text.splitlines()
    wanted = {"syslog.status": "enabled", "syslog.remote.status": "enabled", "syslog.remote.ip": server, "syslog.remote.port": str(PORT)}
    seen = set()
    for index, line in enumerate(lines):
        name = line.split("=", 1)[0]
        if name in wanted:
            lines[index] = f"{name}={wanted[name]}"
            seen.add(name)
        elif name == "resolv.host.1.name":
            hostname = _KEY_SUFFIX.sub(" ", line.split("=", 1)[1]).strip()
            if mode == "key":
                suffix = f" {identity.KEY_PREFIX}{key}"
                hostname = hostname[:HOSTNAME_MAX - len(suffix)].rstrip() + suffix
            lines[index] = f"resolv.host.1.name={hostname}"
    missing = [f"{name}={value}" for name, value in wanted.items() if name not in seen]
    if missing:
        anchor = max((i for i, line in enumerate(lines) if line.startswith("syslog.")), default=len(lines) - 1) + 1
        lines[anchor:anchor] = missing
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _repack(data: bytes, server: str, mode: str, key: str) -> bytes:
    source = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    out = io.BytesIO()
    target = tarfile.open(fileobj=out, mode="w:gz" if data[:2] == b"\x1f\x8b" else "w", format=source.format)
    found = False
    for member in source.getmembers():
        content = source.extractfile(member).read() if member.isfile() else b""
        if member.name.lstrip("./") == "system.cfg":
            content = edit_config(content.decode("utf-8"), server, mode, key).encode("utf-8")
            found = True
        info = tarfile.TarInfo(member.name)
        for attr in ("mode", "mtime", "uid", "gid", "uname", "gname", "type"):
            setattr(info, attr, getattr(member, attr))
        info.size = len(content)
        target.addfile(info, io.BytesIO(content))
    target.close()
    if not found:
        raise UispSyslogError("backup", "Il backup UISP non contiene system.cfg: dispositivo non airOS.")
    return out.getvalue()


def _call(connection, method, path, what, **kwargs):
    """UISP request with multipart/raw bodies (``ops.request`` sends JSON only)."""
    url = f"{connection.base_url.rstrip('/')}{ops.API}{path}"
    try:
        with ops._client(connection) as client:
            response = client.request(method, url, **kwargs)
    except Exception as exc:  # noqa: BLE001 - httpx errors described like the other UISP operations
        raise UispSyslogError("uisp", f"UISP non raggiungibile durante {what}.") from exc
    try:
        ops._check(response, what, write=method != "GET")
    except ops.UispOperationError as exc:
        raise UispSyslogError("uisp", str(exc)) from None
    return response


def _connection(db, device: Device):
    if device.vendor != "ubiquiti" or not device.external_device_id:
        raise UispSyslogError("not_linked", "Collega prima il dispositivo a UISP (scheda UISP).")
    connection = uisp._connection(db, enabled_only=True)
    if connection is None:
        raise UispSyslogError("not_configured", "UISP non è configurato o è disattivato (Integrazioni → UISP).")
    return connection


def live_state(db, device: Device) -> dict:
    """Remote syslog of the device as UISP reads it now; ``supported`` False when UISP does not expose its services."""
    connection = _connection(db, device)
    url = f"{connection.base_url.rstrip('/')}{ops.API}/devices/{device.external_device_id}/services"
    try:
        with ops._client(connection) as client:
            response = client.get(url)
    except Exception:  # noqa: BLE001
        return {"supported": None, "system_log": None}
    if response.status_code != 200:
        return {"supported": False if response.status_code == 404 else None, "system_log": None}
    log = (response.json() or {}).get("systemLog") or {}
    return {"supported": True, "system_log": {"enabled": log.get("enabled"), "server": log.get("server"), "port": log.get("port")}}


def _newest_backup(connection, device, known: set) -> dict | None:
    rows = _call(connection, "GET", f"/devices/{device.external_device_id}/backups", "l'elenco dei backup").json() or []
    fresh = [row for row in rows if row.get("id") not in known]
    return sorted(fresh, key=lambda row: str(row.get("timestamp") or ""))[-1] if fresh else None


def set_level(db, user, device: Device, level: str) -> dict:
    """Minimum severity kept for this device (receiver side, no reboot); audited, commits."""
    value = state_of(device)
    if not value or value.get("status") != "applied":
        raise UispSyslogError("none", "Configura prima il syslog tramite UISP.")
    if level not in LEVELS:
        raise UispSyslogError("level", "Livello non valido.")
    data = dict(device.inventory_data or {})
    data[KEY] = {**value, "level": level}
    device.inventory_data = data
    core.add_event(db, "UISP_SYSLOG_LEVEL_CHANGED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"from": value.get("level") or DEFAULT_LEVEL, "to": level})
    db.commit()
    return data[KEY]


def configure(db, user, device: Device, server: str, mode: str, confirmation: str, level: str = DEFAULT_LEVEL) -> dict:
    """Fresh backup (restore point), changed copy uploaded and applied through UISP; audited, commits."""
    if mode not in MODES:
        raise UispSyslogError("mode", "Modalità di riconoscimento non valida.")
    if level not in LEVELS:
        raise UispSyslogError("level", "Livello non valido.")
    if (confirmation or "").strip() != device_name(device):
        raise UispSyslogError("confirmation", f"Per confermare scrivi esattamente il nome del dispositivo: {device_name(device)}")
    if not server:
        raise UispSyslogError("target", "Indirizzo del ricevitore syslog non determinabile: impostalo in Amministrazione → Syslog.")
    connection = _connection(db, device)
    if live_state(db, device)["supported"] is False:
        raise UispSyslogError("unsupported", "UISP non gestisce i servizi di questo dispositivo: configura il syslog dall'interfaccia airOS.")
    key = identity.device_key(device)
    ext = device.external_device_id
    if mode == "customer_name":
        hostname = ((_call(connection, "GET", f"/devices/{ext}", "la lettura del dispositivo").json() or {}).get("identification") or {}).get("hostname")
        customer = device.customer.name if device.customer else ""
        if not identity.name_tokens(customer) or identity.name_tokens(hostname) != identity.name_tokens(customer):
            raise UispSyslogError("hostname", f"Il nome del CPE in UISP («{hostname or '—'}») non corrisponde al cliente NSM («{customer or '—'}»): "
                                              "usa la chiave NSM o l'indirizzo, oppure rinomina il CPE.")
    known = {row.get("id") for row in _call(connection, "GET", f"/devices/{ext}/backups", "l'elenco dei backup").json() or []}
    _call(connection, "POST", f"/devices/{ext}/backups", "la creazione del backup")
    before = None
    waited = 0
    while before is None and waited <= BACKUP_WAIT_SECONDS:
        before = _newest_backup(connection, device, known)
        if before is None:
            SLEEP(5)
            waited += 5
    if before is None:
        raise UispSyslogError("backup", "UISP non ha completato il backup del dispositivo: riprova tra qualche minuto.")
    original = _call(connection, "GET", f"/devices/{ext}/backups/{before['id']}", "il download del backup").content
    changed = _repack(original, server, mode, key)
    uploaded = _call(connection, "PUT", f"/devices/{ext}/backups", "il caricamento della configurazione",
                     files={"file": ("nsm-syslog.tar.gz", changed, "application/octet-stream")}).json() or {}
    if not uploaded.get("id"):
        raise UispSyslogError("upload", "UISP non ha accettato la configurazione modificata.")
    _call(connection, "POST", f"/devices/{ext}/backups/{uploaded['id']}/apply", "l'applicazione della configurazione")
    now = utcnow()
    value = {"mode": mode, "server": server, "port": PORT, "level": level, "applied_at": now.isoformat(), "by": user.username if user else None,
             "match_name": device.customer.name if mode == "customer_name" and device.customer else None,
             "backup_before": before["id"], "backup_applied": uploaded["id"], "status": "applied"}
    data = dict(device.inventory_data or {})
    data[KEY] = value
    device.inventory_data = data
    core.add_event(db, "UISP_SYSLOG_CONFIGURED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"mode": mode, "server": server, "port": PORT, "level": level, "backup_before": before["id"], "backup_applied": uploaded["id"]})
    db.commit()
    return value


def restore(db, user, device: Device, confirmation: str) -> dict:
    """Apply the restore point taken before the last configuration (the CPE reboots); audited, commits."""
    value = state_of(device)
    if not value or not value.get("backup_before"):
        raise UispSyslogError("none", "Nessun punto di ripristino per questo dispositivo.")
    if (confirmation or "").strip() != device_name(device):
        raise UispSyslogError("confirmation", f"Per confermare scrivi esattamente il nome del dispositivo: {device_name(device)}")
    connection = _connection(db, device)
    _call(connection, "POST", f"/devices/{device.external_device_id}/backups/{value['backup_before']}/apply", "il ripristino della configurazione")
    data = dict(device.inventory_data or {})
    data[KEY] = {**value, "status": "restored", "restored_at": utcnow().isoformat()}
    device.inventory_data = data
    core.add_event(db, "UISP_SYSLOG_RESTORED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"backup": value["backup_before"]})
    db.commit()
    return data[KEY]


# ------------------------------------------------------------------- API --

def _fail(exc: InputError):
    raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc


def _ubiquiti(db, device_id) -> Device:
    device = db.get(Device, device_id)
    if device is None or device.vendor != "ubiquiti":
        raise HTTPException(404)
    return device


@router.get("/devices/{device_id}/syslog/uisp")
def device_state(request: Request, device_id: uuid.UUID):
    from app import acs_syslog

    with SessionLocal() as db:
        user = ui_user(request, db, "monitoring.read")
        device = _ubiquiti(db, device_id)
        try:
            live = live_state(db, device)
            linked = True
        except InputError:
            live, linked = {"supported": None, "system_log": None}, False
        value = state_of(device)
        return {"linked": linked, "target": acs_syslog.target(db, request), "port": PORT, "modes": list(MODES), "default_mode": DEFAULT_MODE,
                "levels": list(LEVELS), "default_level": DEFAULT_LEVEL,
                "name": device_name(device), "customer": device.customer.name if device.customer else None,
                "state": {**{k: value.get(k) for k in ("mode", "server", "port", "applied_at", "by", "status", "restored_at")},
                          "level": value.get("level") or DEFAULT_LEVEL} if value else None,
                "can_restore": bool(value and value.get("backup_before")), **live,
                "can_configure": core.has_permission(user, "devices.write")}


@router.post("/devices/{device_id}/syslog/uisp", dependencies=[Depends(require_csrf)])
def device_configure(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    from app import acs_syslog

    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _ubiquiti(db, device_id)
        try:
            return {"state": configure(db, user, device, acs_syslog.target(db, request), str(body.get("mode") or DEFAULT_MODE),
                                       str(body.get("confirmation") or ""), str(body.get("level") or DEFAULT_LEVEL))}
        except InputError as exc:
            db.rollback()
            _fail(exc)


@router.post("/devices/{device_id}/syslog/uisp/level", dependencies=[Depends(require_csrf)])
def device_level(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _ubiquiti(db, device_id)
        try:
            return {"state": set_level(db, user, device, str(body.get("level") or ""))}
        except InputError as exc:
            db.rollback()
            _fail(exc)


@router.post("/devices/{device_id}/syslog/uisp/restore", dependencies=[Depends(require_csrf)])
def device_restore(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = _ubiquiti(db, device_id)
        try:
            return {"state": restore(db, user, device, str(body.get("confirmation") or ""))}
        except InputError as exc:
            db.rollback()
            _fail(exc)


def install_uisp_syslog(app) -> None:
    app.include_router(router)
