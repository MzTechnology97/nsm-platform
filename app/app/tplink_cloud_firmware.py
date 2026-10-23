"""ACS-08: firmware from TP-Link's own cloud, through TR-069 (TP-Link CPEs exposing ``Device.X_TP_CloudService``).

The CPE asks TP-Link's cloud for newer firmware by itself (the *Online Upgrade* box of its web page) and keeps the
answer in ``Device.X_TP_CloudService.FwUpgradeInfo`` (``Version``, ``ReleaseDate``, ``ReleaseLog``, ``Url``,
``Progress``, ``Status``). Checked on a VX230v (2026-10-23, operator-approved test): setting
``FwUpgradeInfo.NeedStartUpgrade`` and then ``X_TP_CloudService.X_TP_Upgrade_Action`` to true makes the CPE download
that image from the cloud, install it and reboot (0.16.0 → 0.19.0 in about two minutes).

- ``GET /api/ui/v1/devices/{id}/acs/firmware/cloud`` (``devices.read``): what the CPE last reported, from GenieACS
  (nothing is sent to the CPE).
- ``POST .../cloud/check`` (``devices.write``): re-read ``FwUpgradeInfo`` from the CPE now (``refreshObject`` with a
  connection request). The worker also queues that read once a day for each TP-Link CPE (``daily_refresh``), for
  its next periodic contact.
- ``POST .../cloud`` (``devices.write``, typed confirmation as for the image upgrade): the two parameters above, one
  ``setParameterValues`` task each, only when the cloud offers a version different from the installed one. The upgrade
  is followed like the image upgrade (``acs_firmware.refresh_upgrade``: verified from the new ``SoftwareVersion``,
  failed on a CPE fault, unconfirmed after 30 minutes) and audited ``ACS_FIRMWARE_REQUESTED`` with
  ``method: tplink_cloud``.

Nothing is installed automatically; the standard TR-069 ``Download`` of an image stays in ``acs_firmware``.
"""
from __future__ import annotations

import html
import json
import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import acs_diagnostics as nbi
from app import acs_firmware as fw
from app import main as core
from app.db import SessionLocal
from app.models import Device, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
CLOUD = "Device.X_TP_CloudService"
INFO = f"{CLOUD}.FwUpgradeInfo"
START = f"{INFO}.NeedStartUpgrade"
ACTION = f"{CLOUD}.X_TP_Upgrade_Action"
METHOD = "tplink_cloud"
REFRESH_EVERY = timedelta(hours=24)
DAILY_BATCH = 50


def _read(connection, external_id: str) -> dict:
    return nbi._read(connection, external_id, ["_deviceId", "_lastInform", CLOUD, "Device.DeviceInfo.SoftwareVersion"])


def supported(record: dict) -> bool:
    return isinstance(nbi._node(record, CLOUD), dict)


def _notes(value) -> str | None:
    """The release log as plain text (TP-Link sends HTML line breaks)."""
    text = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
    return text[:4000] or None


def offer(record: dict) -> dict | None:
    """What TP-Link's cloud offers this CPE, as the CPE last reported it (None when it reported nothing)."""
    version = nbi._value(record, f"{INFO}.Version")
    if not version:
        return None
    current = fw.software_version(record)
    return {"version": str(version)[:120], "released_at": str(nbi._value(record, f"{INFO}.ReleaseDate") or "")[:20] or None,
            "notes": _notes(nbi._value(record, f"{INFO}.ReleaseLog")), "url": str(nbi._value(record, f"{INFO}.Url") or "")[:500] or None,
            "newer": bool(current) and str(version).strip() != current.strip(),
            "progress": nbi._number(nbi._value(record, f"{INFO}.Progress")), "status": nbi._number(nbi._value(record, f"{INFO}.Status"))}


def _payload(device, connection, can_run: bool) -> dict:
    out = {"supported": False, "offer": None, "current_version": None, "checked_at": None, "last_inform": None, "can_run": can_run,
           "confirm_name": fw._confirm_name(device), "error": None}
    try:
        record = _read(connection, device.external_device_id)
    except nbi.AcsDiagnosticError as exc:
        out["error"] = str(exc)
        return out
    out.update({"supported": supported(record), "offer": offer(record), "current_version": fw.software_version(record),
                "checked_at": (device.inventory_data or {}).get("tplink_cloud_checked_at"), "last_inform": record.get("_lastInform")})
    return out


def request_cloud_upgrade(db, user, device, connection, now: datetime | None = None) -> dict:
    """Start the CPE's own upgrade from TP-Link's cloud (audited); returns the stored entry. Caller commits."""
    now = now or utcnow()
    if ((device.inventory_data or {}).get("acs_firmware") or {}).get("state") == "requested":
        raise HTTPException(409, {"code": "running", "message": "C'è già un aggiornamento firmware in corso su questo CPE."})
    record = _read(connection, device.external_device_id)
    if not supported(record):
        raise HTTPException(422, {"code": "unsupported", "message": "Questo CPE non espone l'aggiornamento dal cloud TP-Link."})
    proposal = offer(record)
    if not proposal or not proposal["newer"]:
        raise HTTPException(422, {"code": "nothing_newer", "message": "Il cloud TP-Link non propone una versione diversa da quella installata: verifica di nuovo."})
    nbi._task(connection, device.external_device_id, {"name": "setParameterValues", "parameterValues": [[START, True, "xsd:boolean"]]})
    started = nbi._task(connection, device.external_device_id, {"name": "setParameterValues", "parameterValues": [[ACTION, True, "xsd:boolean"]]})
    entry = {"state": "requested", "method": METHOD, "file_name": "TP-Link cloud", "expected_version": proposal["version"],
             "from_version": fw.software_version(record), "requested_at": now.isoformat(), "by": user.username if user else None,
             "started": started, "to_version": None, "completed_at": None, "fault": None}
    fw._store(device, entry)
    core.add_event(db, "ACS_FIRMWARE_REQUESTED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"method": METHOD, "from_version": entry["from_version"], "expected_version": proposal["version"], "started": started})
    return entry


def _mark_checked(device, now) -> None:
    data = dict(device.inventory_data or {})
    data["tplink_cloud_checked_at"] = now.isoformat()
    device.inventory_data = data


def _queue_refresh(connection, external_id: str) -> None:
    """``refreshObject`` without a connection request: the CPE runs it at its next periodic contact."""
    try:
        with nbi._client(connection) as client:
            response = client.post(f"/devices/{quote(external_id, safe='')}/tasks", content=json.dumps({"name": "refreshObject", "objectName": INFO}),
                                   headers={"Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        raise nbi.AcsDiagnosticError("GenieACS non raggiungibile.") from exc
    if response.status_code not in (200, 202):
        raise nbi.AcsDiagnosticError(f"GenieACS ha rifiutato il comando (HTTP {response.status_code}).")


def daily_refresh(now: datetime | None = None) -> dict:
    """Worker task: once a day per TP-Link CPE, queue a read of what TP-Link's cloud offers it."""
    now = now or utcnow()
    queued = 0
    with SessionLocal() as db:
        connection = fw.acs._connection(db)
        if connection is None or not connection.is_enabled:
            return {"queued": 0}
        devices = db.scalars(select(Device).where(Device.external_device_id.is_not(None), Device.management_source == "tr069",
                                                  Device.vendor == "tp-link"))
        for device in devices:
            last = (device.inventory_data or {}).get("tplink_cloud_checked_at")
            try:
                due = last is None or now - datetime.fromisoformat(last) >= REFRESH_EVERY
            except (TypeError, ValueError):
                due = True
            if not due:
                continue
            try:
                if supported(_read(connection, device.external_device_id)):
                    _queue_refresh(connection, device.external_device_id)
                    queued += 1
            except nbi.AcsDiagnosticError:
                continue  # GenieACS or this CPE unavailable: retried tomorrow
            _mark_checked(device, now)
            if queued >= DAILY_BATCH:
                break
        db.commit()
    return {"queued": queued}


@router.get("/devices/{device_id}/acs/firmware/cloud")
def device_cloud_firmware(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device, connection = nbi._linked(db, device_id)
        return _payload(device, connection, core.has_permission(user, "devices.write"))


@router.post("/devices/{device_id}/acs/firmware/cloud/check", dependencies=[Depends(require_csrf)])
def device_cloud_firmware_check(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        ui_user(request, db, "devices.write")
        device, connection = nbi._linked(db, device_id)
        try:
            nbi._task(connection, device.external_device_id, {"name": "refreshObject", "objectName": INFO})
        except nbi.AcsDiagnosticError as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        _mark_checked(device, utcnow())
        db.commit()
        return _payload(device, connection, True)


@router.post("/devices/{device_id}/acs/firmware/cloud", dependencies=[Depends(require_csrf)])
def device_cloud_firmware_run(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device, connection = nbi._linked(db, device_id)
        if str((body or {}).get("confirm") or "").strip() != fw._confirm_name(device):
            raise HTTPException(422, {"code": "confirm", "message": "Scrivi il nome dell'apparato per confermare l'aggiornamento."})
        try:
            request_cloud_upgrade(db, user, device, connection)
        except nbi.AcsDiagnosticError as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        db.commit()
        return _payload(device, connection, True)


def install_tplink_cloud_firmware(app) -> None:
    app.include_router(router)
