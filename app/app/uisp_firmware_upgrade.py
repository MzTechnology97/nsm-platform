"""Ubiquiti firmware upgrade through UISP (UBNT-06, operator request 2026-10-23).

Until now NSM only showed the UISP firmware state; the upgrade was done in UISP. The device *Firmware* tab of a
Ubiquiti device linked to UISP now starts it, through the same API UISP's own *Upgrade* uses:

- versions offered: the stable images UISP has for the device's platform (``GET /firmwares``, ``platformId`` = the
  device's ``identification.platformId``, e.g. ``WA``/``XC``) newer than the installed one; the default is the version
  NSM recommends (UISP's latest or the catalog, ``recommended_firmware_version``);
- ``POST /tasks`` with ``{upgrades: [{deviceId, firmwareVersion}], upgradeInMaintenanceWindow}``: UISP queues the
  upgrade (at once, or in the maintenance window configured in UISP) and pushes the image to the device. The token
  needs write permission. Typed confirmation of the device name, like the other upgrades; one at a time per device;
- followed by the worker (``follow_upgrades``) and on the page: *verified* when UISP reports the device on the
  requested version (``identification.firmwareVersion``), *failed* when UISP's ``upgrade.status`` says failed or
  canceled after the request, *unconfirmed* after ``TIMEOUT`` (``MAINTENANCE_TIMEOUT`` in the maintenance window).
  The ``upgrade`` object of the device record alone is not trusted: on the operator's console it still said
  "success 8.7.15" for a device running 8.7.4.

Audit: ``UISP_FIRMWARE_UPGRADE_REQUESTED``, ``UISP_FIRMWARE_UPGRADE_VERIFIED``, ``UISP_FIRMWARE_UPGRADE_FAILED``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app import uisp_operations as ops
from app.db import SessionLocal
from app.models import Device, utcnow
from app.ui_api import require_csrf, ui_user
from app.vendor_cpe import version_tuple

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
TIMEOUT = timedelta(minutes=60)
MAINTENANCE_TIMEOUT = timedelta(hours=48)
KEY = "uisp_upgrade"


def _version(value) -> tuple | None:
    return version_tuple(str(value or "").strip().lstrip("vV"))


def _newer(candidate, installed) -> bool:
    a, b = _version(candidate), _version(installed)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _confirm_name(device) -> str:
    return device.display_name or device.device_identity or device.name


def _record(connection, device) -> dict:
    row = ops.request(connection, "GET", f"/devices/{device.external_device_id}", what="la lettura del dispositivo")
    return row if isinstance(row, dict) else {}


def available(connection, platform: str, installed: str | None) -> list[dict]:
    """Stable UISP images for this platform, newest first; only versions newer than the installed one."""
    rows = ops.request(connection, "GET", "/firmwares", what="l'elenco dei firmware") or []
    seen, out = set(), []
    for row in rows if isinstance(rows, list) else []:
        ident = row.get("identification") or {}
        version = str(ident.get("version") or "")
        if ident.get("platformId") != platform or not ident.get("stable", True) or version in seen or not _version(version):
            continue
        if installed and not _newer(version, installed):
            continue
        seen.add(version)
        out.append({"version": version, "date": row.get("date"), "size": row.get("size"), "compatibility": ident.get("firmwareCompatibility")})
    return sorted(out, key=lambda r: _version(r["version"]) + (0,) * 6, reverse=True)


def _store(device, entry: dict) -> None:
    data = dict(device.inventory_data or {})
    data[KEY] = entry
    device.inventory_data = data


def request_upgrade(db, user, device, connection, version: str, maintenance_window: bool, now: datetime | None = None) -> dict:
    """Queue the upgrade in UISP (audited). Caller commits."""
    now = now or utcnow()
    entry = (device.inventory_data or {}).get(KEY) or {}
    if entry.get("state") == "requested":
        raise ops.UispOperationError("Un aggiornamento è già in corso per questo apparato.")
    record = _record(connection, device)
    ident = record.get("identification") or {}
    installed, platform = ident.get("firmwareVersion") or device.firmware_version, ident.get("platformId") or ""
    if version not in {r["version"] for r in available(connection, platform, installed)}:
        raise ops.UispOperationError(f"UISP non ha la versione {version} per la piattaforma {platform or '?'} o non è più recente di quella installata.")
    ops.request(connection, "POST", "/tasks", what="l'avvio dell'aggiornamento", write=True,
                json_body={"upgradeInMaintenanceWindow": bool(maintenance_window), "upgrades": [{"deviceId": device.external_device_id, "firmwareVersion": version}]})
    entry = {"state": "requested", "version": version, "from_version": installed, "platform": platform, "maintenance_window": bool(maintenance_window),
             "requested_at": now.isoformat(), "requested_by": user.username if user else None, "progress": None}
    _store(device, entry)
    core.add_event(db, "UISP_FIRMWARE_UPGRADE_REQUESTED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"uisp_id": device.external_device_id, "from": installed, "to": version, "maintenance_window": bool(maintenance_window)})
    return entry


def refresh_upgrade(db, device, connection, now: datetime | None = None) -> bool:
    """Advance a requested upgrade from the UISP device record; True when the entry changed."""
    now = now or utcnow()
    entry = dict((device.inventory_data or {}).get(KEY) or {})
    if entry.get("state") != "requested":
        return False
    try:
        record = _record(connection, device)
    except ops.UispOperationError:
        record = None
    requested = datetime.fromisoformat(entry["requested_at"])
    if record:
        running = (record.get("identification") or {}).get("firmwareVersion")
        upgrade = record.get("upgrade") if isinstance(record.get("upgrade"), dict) else {}
        target = upgrade.get("firmwareVersion")
        if target == entry["version"] and isinstance(upgrade.get("progress"), (int, float)):
            entry["progress"] = round(float(upgrade["progress"]), 2)
            entry["uisp_status"] = str(upgrade.get("status") or "")[:40] or None
        if running and _version(running) == _version(entry["version"]):
            device.firmware_version = running
            entry.update({"state": "verified", "finished_at": now.isoformat(), "detail": f"UISP riporta l'apparato sulla versione {running}."})
            _store(device, entry)
            core.add_event(db, "UISP_FIRMWARE_UPGRADE_VERIFIED", customer_id=device.customer_id, device_id=device.id, source="worker", result="success",
                           details={"to": entry["version"], "from": entry.get("from_version")})
            return True
        if target == entry["version"] and str(upgrade.get("status") or "").lower() in ("failed", "canceled", "cancelled") and (now - requested).total_seconds() > 60:
            entry.update({"state": "failed", "finished_at": now.isoformat(), "detail": f"UISP: aggiornamento {upgrade.get('status')}."})
            _store(device, entry)
            core.add_event(db, "UISP_FIRMWARE_UPGRADE_FAILED", customer_id=device.customer_id, device_id=device.id, source="worker", result="failure",
                           severity="warning", details={"to": entry["version"], "uisp_status": upgrade.get("status")})
            return True
    if now - requested > (MAINTENANCE_TIMEOUT if entry.get("maintenance_window") else TIMEOUT):
        entry.update({"state": "unconfirmed", "finished_at": now.isoformat(), "detail": "Nessun cambio di versione osservato in UISP entro il tempo previsto."})
        _store(device, entry)
        core.add_event(db, "UISP_FIRMWARE_UPGRADE_FAILED", customer_id=device.customer_id, device_id=device.id, source="worker", result="failure",
                       severity="warning", details={"to": entry["version"], "detail": "unconfirmed"})
        return True
    _store(device, entry)
    return False


def follow_upgrades(now: datetime | None = None) -> dict:
    """Worker task: follow requested UISP upgrades even when nobody has the device page open."""
    changed = 0
    with SessionLocal() as db:
        for device in db.scalars(select(Device).where(Device.vendor == "ubiquiti", Device.external_device_id.is_not(None))):
            if ((device.inventory_data or {}).get(KEY) or {}).get("state") != "requested":
                continue
            try:
                connection = ops._connection_for(db, device)
            except ops.UispOperationError:
                continue
            if refresh_upgrade(db, device, connection, now):
                changed += 1
        db.commit()
    return {"followed": changed}


# --- Routes ------------------------------------------------------------------------------------

def _device(db, device_id):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, {"code": "not_found", "message": "Apparato non trovato."})
    try:
        return device, ops._connection_for(db, device)
    except ops.UispOperationError as exc:
        raise HTTPException(409, {"code": "not_linked", "message": str(exc)}) from None


def _payload(device, connection, can_run: bool) -> dict:
    out = {"can_run": can_run, "confirm_name": _confirm_name(device), "upgrade": (device.inventory_data or {}).get(KEY),
           "installed": device.firmware_version, "platform": None, "recommended": device.recommended_firmware_version,
           "firmware_status": device.firmware_status, "versions": [], "error": None}
    try:
        ident = _record(connection, device).get("identification") or {}
        out["installed"], out["platform"] = ident.get("firmwareVersion") or device.firmware_version, ident.get("platformId")
        out["versions"] = available(connection, out["platform"] or "", out["installed"])
    except ops.UispOperationError as exc:
        out["error"] = str(exc)
    return out


@router.get("/devices/{device_id}/uisp/firmware")
def device_uisp_firmware(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device, connection = _device(db, device_id)
        if refresh_upgrade(db, device, connection):
            db.commit()
        return _payload(device, connection, core.has_permission(user, "devices.write"))


@router.post("/devices/{device_id}/uisp/firmware", dependencies=[Depends(require_csrf)])
def device_uisp_firmware_run(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device, connection = _device(db, device_id)
        if str((body or {}).get("confirm") or "").strip() != _confirm_name(device):
            raise HTTPException(422, {"code": "confirm", "message": "Scrivi il nome dell'apparato per confermare l'aggiornamento."})
        version = str((body or {}).get("version") or "").strip()[:40]
        if not version:
            raise HTTPException(422, {"code": "no_version", "message": "Scegli la versione."})
        try:
            request_upgrade(db, user, device, connection, version, bool((body or {}).get("maintenance_window")))
        except ops.UispOperationError as exc:
            raise HTTPException(422, {"code": "uisp", "message": str(exc)}) from None
        db.commit()
        return _payload(device, connection, True)


def install_uisp_firmware_upgrade(app) -> None:
    app.include_router(router)
