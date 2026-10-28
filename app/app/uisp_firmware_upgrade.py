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

Bulk (``POST /firmware/uisp-upgrade``, from the firmware worklist): up to ``MAX_BULK`` devices, the target is either
``recommended`` (per device, as preselected on the tab) or one version; each device goes through the same checks as the
single request (``plan_upgrade``), the ones that pass are queued in one ``POST /tasks`` and the others are reported as
skipped with the reason. Typed confirmation ``AGGIORNA <n>``.

Audit: ``UISP_FIRMWARE_UPGRADE_REQUESTED`` (per device), ``UISP_FIRMWARE_BULK_UPGRADE``, ``UISP_FIRMWARE_UPGRADE_VERIFIED``,
``UISP_FIRMWARE_UPGRADE_FAILED``.
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
MAX_BULK = 50
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


def firmwares(connection) -> list:
    rows = ops.request(connection, "GET", "/firmwares", what="l'elenco dei firmware") or []
    return rows if isinstance(rows, list) else []


def available(connection, platform: str, installed: str | None, rows: list | None = None) -> list[dict]:
    """Stable UISP images for this platform, newest first; only versions newer than the installed one.

    ``rows``: the ``/firmwares`` list already read (a bulk request reads it once)."""
    seen, out = set(), []
    for row in firmwares(connection) if rows is None else rows:
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


class UpgradeRefused(ops.UispOperationError):
    """A device that cannot be upgraded now; ``code`` is the reason shown per row of a bulk request."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def plan_upgrade(device, connection, version: str | None, rows: list | None = None) -> dict:
    """Check one device before queueing: no upgrade running, ``version`` offered by UISP for its platform.

    ``version`` None = the recommended one, as preselected on the tab: NSM's recommendation when UISP offers it,
    else UISP's newest stable image."""
    if ((device.inventory_data or {}).get(KEY) or {}).get("state") == "requested":
        raise UpgradeRefused("running", "Un aggiornamento è già in corso per questo apparato.")
    ident = _record(connection, device).get("identification") or {}
    installed, platform = ident.get("firmwareVersion") or device.firmware_version, ident.get("platformId") or ""
    versions = [r["version"] for r in available(connection, platform, installed, rows)]
    if version is None:
        if not versions:
            raise UpgradeRefused("no_version", f"UISP non ha versioni stabili più recenti di {installed or '?'} per la piattaforma {platform or '?'}.")
        version = device.recommended_firmware_version if device.recommended_firmware_version in versions else versions[0]
    elif version not in versions:
        raise UpgradeRefused("version_unavailable", f"UISP non ha la versione {version} per la piattaforma {platform or '?'} o non è più recente di quella installata.")
    return {"version": version, "installed": installed, "platform": platform}


def _queue(connection, items: list[tuple], maintenance_window: bool) -> None:
    """One UISP task for all the ``(device, plan)`` pairs."""
    ops.request(connection, "POST", "/tasks", what="l'avvio dell'aggiornamento", write=True,
                json_body={"upgradeInMaintenanceWindow": bool(maintenance_window),
                           "upgrades": [{"deviceId": device.external_device_id, "firmwareVersion": plan["version"]} for device, plan in items]})


def _requested(db, user, device, plan: dict, maintenance_window: bool, now: datetime, bulk: bool = False) -> dict:
    entry = {"state": "requested", "version": plan["version"], "from_version": plan["installed"], "platform": plan["platform"],
             "maintenance_window": bool(maintenance_window), "requested_at": now.isoformat(), "requested_by": user.username if user else None, "progress": None}
    _store(device, entry)
    details = {"uisp_id": device.external_device_id, "from": plan["installed"], "to": plan["version"], "maintenance_window": bool(maintenance_window)}
    core.add_event(db, "UISP_FIRMWARE_UPGRADE_REQUESTED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={**details, "bulk": True} if bulk else details)
    return entry


def request_upgrade(db, user, device, connection, version: str, maintenance_window: bool, now: datetime | None = None) -> dict:
    """Queue the upgrade in UISP (audited). Caller commits."""
    plan = plan_upgrade(device, connection, version)
    _queue(connection, [(device, plan)], maintenance_window)
    return _requested(db, user, device, plan, maintenance_window, now or utcnow())


def request_bulk(db, user, device_ids: list, target: str | None, maintenance_window: bool, now: datetime | None = None) -> dict:
    """Queue the upgrade of several devices in one UISP task; the devices that cannot go are skipped. Caller commits.

    ``target`` None = the recommended version of each device."""
    now = now or utcnow()
    planned, skipped, connection, rows = [], [], None, None
    for device_id in device_ids:
        device = db.get(Device, device_id)
        if device is None:
            skipped.append({"id": str(device_id), "name": None, "code": "not_found", "message": "Apparato non trovato."})
            continue
        name = _confirm_name(device)
        try:
            connection = ops._connection_for(db, device)
        except ops.UispOperationError as exc:
            skipped.append({"id": str(device.id), "name": name, "code": "not_linked", "message": str(exc)})
            continue
        if rows is None:
            rows = firmwares(connection)  # read once; a failure here stops the whole request
        try:
            planned.append((device, plan_upgrade(device, connection, target, rows)))
        except UpgradeRefused as exc:
            skipped.append({"id": str(device.id), "name": name, "code": exc.code, "message": str(exc)})
        except ops.UispOperationError as exc:
            skipped.append({"id": str(device.id), "name": name, "code": "uisp", "message": str(exc)})
    if planned:
        _queue(connection, planned, maintenance_window)
    started = []
    for device, plan in planned:
        _requested(db, user, device, plan, maintenance_window, now, bulk=True)
        started.append({"id": str(device.id), "name": _confirm_name(device), "version": plan["version"], "from_version": plan["installed"]})
    core.add_event(db, "UISP_FIRMWARE_BULK_UPGRADE", actor=user, source="portal", result="success" if started else "failure",
                   severity="info" if started else "warning",
                   details={"target": target or "recommended", "maintenance_window": bool(maintenance_window), "requested": len(device_ids),
                            "started": len(started), "skipped": len(skipped), "devices": [r["id"] for r in started],
                            "skip_codes": sorted({r["code"] for r in skipped})})
    return {"target": target or "recommended", "maintenance_window": bool(maintenance_window), "started": started, "skipped": skipped}


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


@router.post("/firmware/uisp-upgrade", dependencies=[Depends(require_csrf)])
def firmware_uisp_bulk_upgrade(request: Request, body: dict = Body(default={})):
    """Bulk upgrade from the firmware worklist: ``{device_ids, target, maintenance_window, confirm: "AGGIORNA <n>"}``."""
    body = body or {}
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        ids: list[uuid.UUID] = []
        for value in body.get("device_ids") or []:
            try:
                device_id = uuid.UUID(str(value))
            except ValueError:
                continue
            if device_id not in ids:
                ids.append(device_id)
        if not ids:
            raise HTTPException(422, {"code": "empty", "message": "Seleziona almeno un apparato."})
        if len(ids) > MAX_BULK:
            raise HTTPException(422, {"code": "too_many", "message": f"Al massimo {MAX_BULK} apparati per volta."})
        if str(body.get("confirm") or "").strip() != f"AGGIORNA {len(ids)}":
            raise HTTPException(422, {"code": "confirm", "message": f"Scrivi AGGIORNA {len(ids)} per confermare."})
        target = str(body.get("target") or "recommended").strip()[:40]
        try:
            result = request_bulk(db, user, ids, None if target == "recommended" else target, bool(body.get("maintenance_window")))
        except ops.UispOperationError as exc:
            raise HTTPException(422, {"code": "uisp", "message": str(exc)}) from None
        db.commit()
        return result


def install_uisp_firmware_upgrade(app) -> None:
    app.include_router(router)
