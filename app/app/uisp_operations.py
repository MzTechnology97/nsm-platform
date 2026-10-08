"""UISP device operations (UBNT-08, step 2): reboot, configuration backup, interfaces and events.

All calls go to the UISP Network API v2.1 of the configured console with the
same token as the read-only connector.  Reboot and backup need a token with
write permission; UISP answers 401/403 otherwise and NSM says so.

- **Reboot**: ``POST /devices/{id}/restart`` after an explicit confirmation.
  NSM records the uptime before the request and verifies the reboot on the
  device itself (``GET /devices/{id}``): verified when the uptime restarts,
  failed after ``REBOOT_TIMEOUT``.
- **Backup**: ``POST /devices/{id}/backups`` then download of the newest
  backup (``GET /devices/{id}/backups`` + ``/backups/{backupId}``).  The file
  is archived in NSM like the MikroTik exports (BackupRun + BackupArtifact,
  SHA-256, audit) and counts as protection for backup policies with the
  *Connector Ubiquiti* option.
- **Interfaces** (``GET /devices/{id}/interfaces``) and **events**
  (``GET /logs?deviceId=``) are read on demand and kept on the device.

Endpoint names follow the UISP API documentation (``/nms/api-docs``); field
names are read defensively and must be confirmed on a real console.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app import uisp_connector as uisp
from app.backup_models import BackupArtifact
from app.backup_storage import storage_root
from app.db import SessionLocal
from app.models import BackupRun, Device, utcnow
from app.secret_vault import decrypt_text
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
API = "/nms/api/v2.1"
TIMEOUT = 30.0
MAX_BACKUP_BYTES = 50 * 1024 * 1024
REBOOT_TIMEOUT = timedelta(minutes=20)
REBOOT_CONFIRM = "RIAVVIA"
MAX_INTERFACES = 64
MAX_EVENTS = 50
ARTIFACT_TYPE = "uisp_backup"


class UispOperationError(RuntimeError):
    pass


# --- HTTP ------------------------------------------------------------------------------------------

def _client(connection):
    try:
        token = decrypt_text(connection.secret_encrypted)
    except ValueError as exc:
        raise UispOperationError("Impossibile decifrare il token UISP configurato.") from exc
    return httpx.Client(timeout=TIMEOUT, verify=bool(connection.verify_tls), follow_redirects=False,
                        headers={"Accept": "application/json", "x-auth-token": token})


def _check(response, what: str, write: bool = False):
    if response.status_code in (401, 403):
        raise UispOperationError(f"UISP ha negato {what}: il token deve avere i permessi di {'scrittura' if write else 'lettura'}."
                                 if write else f"UISP ha negato {what}: token non autorizzato.")
    if response.status_code == 404:
        raise UispOperationError(f"UISP non espone {what} per questo dispositivo o questa versione della console (HTTP 404).")
    if 300 <= response.status_code < 400:
        raise UispOperationError("UISP ha risposto con un redirect: configura l'URL finale della console.")
    if response.status_code >= 400:
        raise UispOperationError(f"UISP ha rifiutato {what}: HTTP {response.status_code} {response.text[:160]}")


def request(connection, method: str, path: str, *, what: str, write: bool = False, binary: bool = False, json_body=None):
    url = f"{connection.base_url.rstrip('/')}{API}{path}"
    try:
        with _client(connection) as client:
            response = client.request(method, url, json=json_body)
    except httpx.HTTPError as exc:
        raise UispOperationError(uisp.describe_http_error(exc, url, connection.verify_tls)) from exc
    _check(response, what, write)
    if binary:
        if len(response.content) > MAX_BACKUP_BYTES:
            raise UispOperationError("Backup UISP troppo grande per essere archiviato.")
        return response.content
    if not response.content:
        return None
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        return None


def _connection_for(db, device):
    connection = uisp._connection(db)
    if connection is None or not connection.is_enabled:
        raise UispOperationError("Il connettore UISP non è configurato o è disabilitato.")
    if device.vendor != "ubiquiti" or not device.external_device_id:
        raise UispOperationError("L'apparato non è associato a un dispositivo UISP.")
    return connection


def _dig(row, *paths):
    for path in paths:
        value = row
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if value not in (None, ""):
            return value
    return None


def _uptime(row) -> int | None:
    value = _dig(row, "overview.uptime", "uptime")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# --- Reboot ----------------------------------------------------------------------------------------

def request_reboot(db, device, actor) -> dict:
    connection = _connection_for(db, device)
    state = (device.inventory_data or {}).get("uisp_reboot") or {}
    if state.get("status") == "requested":
        raise UispOperationError("Un riavvio è già in verifica.")
    before = None
    try:
        before = _uptime(request(connection, "GET", f"/devices/{device.external_device_id}", what="la lettura del dispositivo"))
    except UispOperationError:
        before = ((device.inventory_data or {}).get("uisp") or {}).get("metrics", {}).get("uptime_seconds")
    request(connection, "POST", f"/devices/{device.external_device_id}/restart", what="il riavvio", write=True)
    now = utcnow()
    state = {"status": "requested", "requested_at": now.isoformat(), "uptime_before": before, "requested_by": actor.username if actor else None}
    data = dict(device.inventory_data or {})
    data["uisp_reboot"] = state
    device.inventory_data = data
    core.add_event(db, "UISP_REBOOT_REQUESTED", actor=actor, customer_id=device.customer_id, device_id=device.id,
                   details={"uisp_id": device.external_device_id, "uptime_before": before}, source="portal")
    return state


def _finish_reboot(db, device, status: str, detail: str, now):
    data = dict(device.inventory_data or {})
    state = dict(data.get("uisp_reboot") or {})
    state.update({"status": status, "verified_at" if status == "verified" else "failed_at": now.isoformat(), "detail": detail})
    data["uisp_reboot"] = state
    device.inventory_data = data
    core.add_event(db, "UISP_REBOOT_VERIFIED" if status == "verified" else "UISP_REBOOT_FAILED", customer_id=device.customer_id, device_id=device.id,
                   details={"detail": detail}, severity="info" if status == "verified" else "warning",
                   result="success" if status == "verified" else "failure", source="worker")


def verify_reboots(now=None) -> dict:
    """Worker: verify requested reboots from the device uptime read on UISP."""
    now = now or utcnow()
    stats = {"verified": 0, "failed": 0, "waiting": 0}
    with SessionLocal() as db:
        for device in db.scalars(select(Device).where(Device.vendor == "ubiquiti", Device.external_device_id.is_not(None))):
            state = (device.inventory_data or {}).get("uisp_reboot") or {}
            if state.get("status") != "requested":
                continue
            requested = datetime.fromisoformat(state["requested_at"])
            elapsed = (now - requested).total_seconds()
            try:
                row = request(_connection_for(db, device), "GET", f"/devices/{device.external_device_id}", what="la lettura del dispositivo")
                uptime = _uptime(row)
            except UispOperationError:
                uptime = None
            before = state.get("uptime_before")
            if uptime is not None and elapsed >= 60 and (uptime < elapsed or (isinstance(before, int) and uptime < before)):
                _finish_reboot(db, device, "verified", f"uptime ripartito ({uptime} s dopo la richiesta)", now)
                stats["verified"] += 1
            elif now - requested > REBOOT_TIMEOUT:
                _finish_reboot(db, device, "failed", "nessun riavvio osservato entro 20 minuti", now)
                stats["failed"] += 1
            else:
                stats["waiting"] += 1
        db.commit()
    return stats


# --- Backup ----------------------------------------------------------------------------------------

def _backups(connection, device) -> list[dict]:
    rows = request(connection, "GET", f"/devices/{device.external_device_id}/backups", what="l'elenco dei backup") or []
    if isinstance(rows, dict):
        rows = rows.get("items") or rows.get("backups") or []
    return [r for r in rows if isinstance(r, dict) and _dig(r, "id", "identification.id")]


def _stamp(row) -> str:
    return str(_dig(row, "timestamp", "date", "createdAt", "created") or "")


def run_backup(db, device, actor=None, policy=None, trigger="manual") -> BackupRun:
    """Create a backup on UISP, download it and archive it in NSM (caller commits)."""
    connection = _connection_for(db, device)
    now = utcnow()
    run = BackupRun(device_id=device.id, policy_id=policy.id if policy else None, started_at=now, status="in_progress", backup_type=ARTIFACT_TYPE)
    db.add(run)
    db.flush()
    try:
        known = {str(_dig(r, "id", "identification.id")) for r in _backups(connection, device)}
        request(connection, "POST", f"/devices/{device.external_device_id}/backups", what="la creazione del backup", write=True)
        rows = _backups(connection, device)
        fresh = [r for r in rows if str(_dig(r, "id", "identification.id")) not in known] or rows
        if not fresh:
            raise UispOperationError("UISP non ha restituito nessun backup dopo la creazione.")
        newest = sorted(fresh, key=_stamp)[-1]
        backup_id = str(_dig(newest, "id", "identification.id"))
        content = request(connection, "GET", f"/devices/{device.external_device_id}/backups/{backup_id}", what="il download del backup", binary=True)
        if not content:
            raise UispOperationError("Il backup scaricato da UISP è vuoto.")
        extension = ".tar.gz" if content[:2] == b"\x1f\x8b" else ".bin"
        label = re.sub(r"[^A-Za-z0-9._-]+", "-", device.device_identity or device.name or "ubiquiti").strip("-._")[:60] or "ubiquiti"
        filename = f"{now.strftime('%Y%m%dT%H%M%SZ')}_{label}_uisp{extension}"
        folder = storage_root() / "devices" / str(device.id) / now.strftime("%Y") / now.strftime("%m")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / filename
        temp = folder / (filename + ".part")
        temp.write_bytes(content)
        os.replace(temp, path)
        digest = hashlib.sha256(content).hexdigest()
        db.add(BackupArtifact(run_id=run.id, artifact_type=ARTIFACT_TYPE, filename=filename,
                              storage_path=str(path.resolve().relative_to(storage_root())), size_bytes=len(content), sha256=digest))
        run.status, run.completed_at, run.size_bytes, run.sha256 = "success", utcnow(), len(content), digest
        core.add_event(db, "UISP_BACKUP_ARCHIVED", actor=actor, customer_id=device.customer_id, device_id=device.id,
                       details={"run_id": str(run.id), "uisp_backup_id": backup_id, "filename": filename, "size_bytes": len(content), "sha256": digest,
                                "trigger": trigger}, source="portal" if actor else "scheduler")
    except UispOperationError as exc:
        run.status, run.completed_at, run.error_message = "failed", utcnow(), str(exc)[:2000]
        core.add_event(db, "UISP_BACKUP_FAILED", actor=actor, customer_id=device.customer_id, device_id=device.id,
                       details={"run_id": str(run.id), "error": str(exc)[:300], "trigger": trigger}, severity="warning", result="failure",
                       source="portal" if actor else "scheduler")
    return run


def schedule_due_backups(db, now) -> int:
    """Scheduled UISP backups for Ubiquiti devices whose effective policy enables the connector method."""
    from app import backup_maintenance as scheduler
    from app.backup_capabilities import backup_readiness

    queued = 0
    for device in db.scalars(select(Device).where(Device.vendor == "ubiquiti", Device.external_device_id.is_not(None))):
        policy, settings_map = scheduler._device_policy(db, device)
        if not policy:
            continue
        policy_settings = settings_map.get(policy.id)
        if not backup_readiness(db, device, policy, policy_settings).executable:
            continue
        occurrence = scheduler.schedule_occurrence(now, policy_settings)
        if occurrence is None or occurrence > now:
            continue
        done = db.scalar(select(BackupRun.id).where(BackupRun.device_id == device.id, BackupRun.policy_id == policy.id, BackupRun.started_at >= occurrence))
        if done:
            continue
        run_backup(db, device, policy=policy, trigger="schedule")
        queued += 1
    return queued


# --- Interfaces and events -------------------------------------------------------------------------

def fetch_interfaces(db, device) -> list[dict]:
    rows = request(_connection_for(db, device), "GET", f"/devices/{device.external_device_id}/interfaces", what="l'elenco delle interfacce") or []
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        out.append({
            "name": str(_dig(row, "identification.name", "name") or "?")[:40],
            "label": str(_dig(row, "identification.displayName", "identification.description", "displayName") or "")[:60],
            "type": str(_dig(row, "identification.type", "type") or "")[:20],
            "enabled": _dig(row, "enabled", "status.enabled") is not False,
            "status": str(_dig(row, "status.status", "status") or "")[:20],
            "plugged": _dig(row, "status.plugged", "plugged"),
            "speed": str(_dig(row, "status.currentSpeed", "status.speed", "speed") or "")[:20],
            "mtu": _dig(row, "mtu"),
            "rx_bps": _dig(row, "statistics.rxrate", "statistics.rxRate"),
            "tx_bps": _dig(row, "statistics.txrate", "statistics.txRate"),
            "errors": _dig(row, "statistics.errors", "statistics.rxErrors"),
        })
    return out[:MAX_INTERFACES]


def fetch_events(db, device) -> list[dict]:
    payload = request(_connection_for(db, device), "GET", f"/logs?deviceId={device.external_device_id}&count={MAX_EVENTS}&page=1", what="il registro eventi")
    rows = payload.get("items") if isinstance(payload, dict) else payload
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        out.append({"at": str(_dig(row, "timestamp", "date", "createdAt") or "")[:40], "level": str(_dig(row, "level", "severity") or "")[:20],
                    "type": str(_dig(row, "type", "tag") or "")[:40], "message": str(_dig(row, "message", "text") or "")[:500]})
    return out[:MAX_EVENTS]


def _store(device, key, items):
    data = dict(device.inventory_data or {})
    data[key] = {"items": items, "fetched_at": utcnow().isoformat()}
    device.inventory_data = data


# --- Routes ----------------------------------------------------------------------------------------

def _device(db, device_id) -> Device:
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404)
    return device


@router.post("/devices/{device_id}/uisp/reboot", name="uisp_device_reboot")
def uisp_reboot(request_: Request, device_id: uuid.UUID, csrf: str = Form(...), confirmation: str = Form("")):
    validate_csrf(request_, csrf)
    back = f"/devices/{device_id}/uisp#uisp-operations"
    with SessionLocal() as db:
        user = core.require_permission(request_, db, "firmware.execute")
        device = _device(db, device_id)
        if confirmation.strip() != REBOOT_CONFIRM:
            return flash_redirect(request_, back, "warning", f"Per confermare scrivi esattamente {REBOOT_CONFIRM}.", title="Riavvio non inviato")
        try:
            request_reboot(db, device, user)
        except UispOperationError as exc:
            db.rollback()
            return flash_redirect(request_, back, "danger", str(exc), title="Riavvio non inviato")
        db.commit()
    return flash_redirect(request_, back, "success", "Riavvio inviato tramite UISP: NSM verifica il ritorno online entro 20 minuti.", title="Riavvio richiesto")


@router.post("/devices/{device_id}/uisp/backup", name="uisp_device_backup")
def uisp_backup(request_: Request, device_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request_, csrf)
    back = f"/devices/{device_id}/uisp#uisp-operations"
    with SessionLocal() as db:
        user = core.require_permission(request_, db, "backup.execute")
        device = _device(db, device_id)
        try:
            _connection_for(db, device)
        except UispOperationError as exc:
            return flash_redirect(request_, back, "danger", str(exc), title="Backup non eseguito")
        run = run_backup(db, device, actor=user)
        db.commit()
        ok, error = run.status == "success", run.error_message
    if ok:
        return flash_redirect(request_, back, "success", "Backup scaricato da UISP e archiviato in NSM (vedi Backup dell'apparato).", title="Backup completato")
    return flash_redirect(request_, back, "danger", error or "Backup non riuscito.", title="Backup non riuscito")


@router.post("/devices/{device_id}/uisp/interfaces", name="uisp_device_interfaces")
def uisp_interfaces(request_: Request, device_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request_, csrf)
    back = f"/devices/{device_id}/uisp#uisp-interfaces"
    with SessionLocal() as db:
        core.require_permission(request_, db, "devices.read")
        device = _device(db, device_id)
        try:
            _store(device, "uisp_interfaces", fetch_interfaces(db, device))
        except UispOperationError as exc:
            return flash_redirect(request_, back, "danger", str(exc), title="Interfacce non lette")
        db.commit()
    return flash_redirect(request_, back, "success", "Interfacce aggiornate da UISP.", title="Interfacce")


@router.post("/devices/{device_id}/uisp/events", name="uisp_device_events")
def uisp_events(request_: Request, device_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request_, csrf)
    back = f"/devices/{device_id}/uisp#uisp-events"
    with SessionLocal() as db:
        core.require_permission(request_, db, "devices.read")
        device = _device(db, device_id)
        try:
            _store(device, "uisp_events", fetch_events(db, device))
        except UispOperationError as exc:
            return flash_redirect(request_, back, "danger", str(exc), title="Eventi non letti")
        db.commit()
    return flash_redirect(request_, back, "success", "Eventi UISP aggiornati.", title="Eventi")


def scheduled_backups(now=None) -> dict:
    """Worker task: UISP backups due by policy (idempotent per policy occurrence)."""
    now = now or utcnow()
    with SessionLocal() as db:
        done = schedule_due_backups(db, now)
        db.commit()
    return {"backups": done}


def install_uisp_operations(app) -> None:
    app.include_router(router)
    core.templates.env.globals["uisp_reboot_confirm"] = REBOOT_CONFIRM
