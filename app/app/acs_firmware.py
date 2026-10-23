"""ACS-08: firmware upgrade of TR-069 CPEs through GenieACS.

- The operator uploads the vendor image (``.bin`` from the vendor's site) on the
  device *ACS* tab, or picks one already in GenieACS for the same model. NSM
  stores it in GenieACS (NBI ``PUT /files/{name}`` with ``fileType`` "1 Firmware
  Upgrade Image", OUI, product class and the expected version) and queues a
  ``download`` task with a connection request. The CPE fetches the file from the
  GenieACS file server (port 7567, same host as the ACS), installs it and
  reboots.
- Typed confirmation (device name), ``devices.write``; one upgrade at a time per
  CPE. Audited ``ACS_FIRMWARE_REQUESTED``.
- Followed on ``GET``: ``verified`` when GenieACS reports a different
  ``SoftwareVersion`` after the request (``matches`` tells whether it is the
  expected one), ``failed`` when GenieACS records a fault for the CPE after the
  request, ``unconfirmed`` after ``GIVE_UP``. Audited ``ACS_FIRMWARE_VERIFIED`` /
  ``ACS_FIRMWARE_FAILED``. State in ``inventory_data["acs_firmware"]``.
- ``request_upgrade`` and ``refresh_upgrade`` take no request, so campaigns
  over many CPEs can reuse them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app import acs_diagnostics as nbi
from app import genieacs_connector as acs
from app import main as core
from app.db import SessionLocal
from app.models import Device, User, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
FILE_TYPE = "1 Firmware Upgrade Image"
MAX_IMAGE = 128 * 1024 * 1024
MIN_IMAGE = 64 * 1024
GIVE_UP = timedelta(minutes=30)
UPLOAD_TIMEOUT = 300
ROOTS = {"tr181": "Device", "tr098": "InternetGatewayDevice"}


class FirmwareError(RuntimeError):
    pass


def _identity(record: dict) -> dict:
    ident = record.get("_deviceId") or {}
    return {"manufacturer": ident.get("_Manufacturer"), "oui": ident.get("_OUI"), "product_class": ident.get("_ProductClass"),
            "serial": ident.get("_SerialNumber")}


def _model(record: dict) -> str:
    return "tr181" if isinstance(record.get("Device"), dict) else "tr098"


def software_version(record: dict) -> str | None:
    value = nbi._value(record, f"{ROOTS[_model(record)]}.DeviceInfo.SoftwareVersion")
    return str(value) if value not in (None, "") else None


def _read_device(connection, external_id: str) -> dict:
    return nbi._read(connection, external_id, ["_deviceId", "_lastBoot", "_lastInform", "Device.DeviceInfo.SoftwareVersion",
                                               "InternetGatewayDevice.DeviceInfo.SoftwareVersion"])


def list_files(connection, product_class: str | None = None) -> list[dict]:
    """Firmware images held by GenieACS (for one model when ``product_class`` is given)."""
    query = {"metadata.fileType": FILE_TYPE}
    if product_class:
        query["metadata.productClass"] = product_class
    try:
        with nbi._client(connection) as client:
            response = client.get("/files", params={"query": json.dumps(query)})
    except (httpx.HTTPError, nbi.AcsDiagnosticError) as exc:
        raise FirmwareError("GenieACS non raggiungibile.") from exc
    if response.status_code != 200:
        raise FirmwareError(f"GenieACS ha risposto HTTP {response.status_code} leggendo i file.")
    rows = []
    for row in response.json() or []:
        meta = row.get("metadata") or {}
        rows.append({"name": row.get("_id") or row.get("filename"), "size": row.get("length"), "uploaded_at": row.get("uploadDate"),
                     "product_class": meta.get("productClass"), "oui": meta.get("oui"), "version": meta.get("version")})
    return sorted(rows, key=lambda r: str(r.get("uploaded_at") or ""), reverse=True)


def file_name_for(identity: dict, original: str, digest: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", original or "firmware.bin").strip("-.")[:80] or "firmware.bin"
    product = re.sub(r"[^A-Za-z0-9._-]+", "-", identity.get("product_class") or "cpe")[:40]
    return f"nsm-{product}-{digest[:10]}-{stem}"


def store_file(connection, identity: dict, name: str, path: str, version: str | None) -> None:
    """Put the image in GenieACS (NBI ``PUT /files``), streamed from ``path``."""
    headers = {"fileType": FILE_TYPE, "oui": identity.get("oui") or "", "productClass": identity.get("product_class") or "",
               "version": version or "", "Content-Type": "application/octet-stream"}
    try:
        with nbi._client(connection) as client, open(path, "rb") as body:
            response = client.put(f"/files/{quote(name, safe='')}", content=body, headers=headers, timeout=UPLOAD_TIMEOUT)
    except httpx.HTTPError as exc:
        raise FirmwareError("Caricamento del firmware in GenieACS non riuscito.") from exc
    if response.status_code not in (200, 201, 204):
        raise FirmwareError(f"GenieACS ha rifiutato il file (HTTP {response.status_code}).")


def _faults_since(connection, external_id: str, since: datetime) -> list[dict]:
    try:
        with nbi._client(connection) as client:
            response = client.get("/faults", params={"query": json.dumps({"device": external_id})})
    except httpx.HTTPError:
        return []
    if response.status_code != 200:
        return []
    out = []
    for fault in response.json() or []:
        stamp = str(fault.get("timestamp") or "")
        try:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when >= since:
            out.append({"code": str(fault.get("code") or "")[:80], "message": str(fault.get("message") or "")[:300], "at": when.isoformat()})
    return out


def _store(device, entry: dict) -> None:
    data = dict(device.inventory_data or {})
    data["acs_firmware"] = entry
    device.inventory_data = data


def request_upgrade(db, user, device, connection, file_name: str, expected_version: str | None = None, now: datetime | None = None) -> dict:
    """Queue the firmware download on the CPE (audited); returns the stored entry. Caller commits."""
    now = now or utcnow()
    current = (device.inventory_data or {}).get("acs_firmware") or {}
    if current.get("state") == "requested":
        raise HTTPException(409, {"code": "running", "message": "C'è già un aggiornamento firmware in corso su questo CPE."})
    record = _read_device(connection, device.external_device_id)
    identity = _identity(record)
    files = {f["name"]: f for f in list_files(connection)}
    chosen = files.get(file_name)
    if chosen is None:
        raise HTTPException(404, {"code": "no_file", "message": "Il file firmware non è (più) in GenieACS."})
    if chosen.get("product_class") and identity.get("product_class") and chosen["product_class"] != identity["product_class"]:
        raise HTTPException(422, {"code": "wrong_model", "message": f"Il file è per {chosen['product_class']}, il CPE è un {identity['product_class']}."})
    started = nbi._task(connection, device.external_device_id, {"name": "download", "fileType": FILE_TYPE, "fileName": file_name})
    entry = {"state": "requested", "file_name": file_name, "expected_version": expected_version or chosen.get("version") or None,
             "from_version": software_version(record), "requested_at": now.isoformat(), "by": user.username if user else None,
             "started": started, "to_version": None, "completed_at": None, "fault": None}
    _store(device, entry)
    core.add_event(db, "ACS_FIRMWARE_REQUESTED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"file": file_name, "from_version": entry["from_version"], "expected_version": entry["expected_version"], "started": started})
    return entry


def refresh_upgrade(db, device, connection, now: datetime | None = None) -> bool:
    """Follow a requested upgrade; True when something changed. Caller commits."""
    now = now or utcnow()
    entry = dict((device.inventory_data or {}).get("acs_firmware") or {})
    if entry.get("state") != "requested":
        return False
    requested = datetime.fromisoformat(entry["requested_at"])
    try:
        record = _read_device(connection, device.external_device_id)
    except nbi.AcsDiagnosticError:
        return False
    version = software_version(record)
    faults = _faults_since(connection, device.external_device_id, requested)
    if version and version != entry.get("from_version"):
        expected = entry.get("expected_version")
        entry.update({"state": "verified", "to_version": version, "completed_at": now.isoformat(),
                      "matches": (expected.lower() in version.lower()) if expected else None})
        core.add_event(db, "ACS_FIRMWARE_VERIFIED", customer_id=device.customer_id, device_id=device.id, source="worker",
                       details={"from_version": entry.get("from_version"), "to_version": version, "file": entry.get("file_name")})
    elif faults:
        entry.update({"state": "failed", "fault": faults[0], "completed_at": now.isoformat()})
        core.add_event(db, "ACS_FIRMWARE_FAILED", customer_id=device.customer_id, device_id=device.id, source="worker", result="failure",
                       details={"file": entry.get("file_name"), "code": faults[0]["code"]})
    elif now - requested > GIVE_UP:
        entry.update({"state": "unconfirmed", "completed_at": now.isoformat()})
    else:
        return False
    _store(device, entry)
    return True


def follow_upgrades(now: datetime | None = None) -> dict:
    """Worker task: follow requested upgrades even when nobody has the device page open."""
    from sqlalchemy import select

    now = now or utcnow()
    changed = 0
    with SessionLocal() as db:
        connection = acs._connection(db)
        if connection is None or not connection.is_enabled:
            return {"followed": 0}
        for device in db.scalars(select(Device).where(Device.external_device_id.is_not(None))):
            if ((device.inventory_data or {}).get("acs_firmware") or {}).get("state") == "requested" and refresh_upgrade(db, device, connection, now):
                changed += 1
        db.commit()
    return {"followed": changed}


def _confirm_name(device) -> str:
    return device.display_name or device.device_identity or device.name


def catalog_info(db, device) -> dict | None:
    """The newest release of the firmware catalog for this CPE (``vendor_firmware.evaluate``), as on the other devices' firmware tab."""
    info = (device.inventory_data or {}).get("vendor_firmware") or {}
    if not info.get("latest"):
        return None
    from app.vendor_firmware import SOURCE_LABELS
    from app.vendor_firmware_models import VendorFirmwareRelease

    release = None
    try:
        release = db.get(VendorFirmwareRelease, uuid.UUID(str(info.get("release_id"))))
    except (TypeError, ValueError):
        pass
    return {"latest": info["latest"], "source": SOURCE_LABELS.get(info.get("source"), info.get("source")), "security": bool(info.get("security")),
            "status": device.firmware_status, "newer": device.firmware_status in ("update_available", "security_update"),
            # The image can be downloaded from the vendor's site by NSM (tplink_firmware.fetch_image).
            "fetchable": info.get("source") == "vendor_site",
            "released_at": release.released_at.isoformat() if release and release.released_at else None,
            "notes_url": release.notes_url if release else None}


def _payload(db, device, connection, can_run: bool) -> dict:
    entry = (device.inventory_data or {}).get("acs_firmware")
    out = {"linked": True, "can_run": can_run, "confirm_name": _confirm_name(device), "upgrade": entry, "files": [], "files_error": None,
           "current_version": None, "product_class": None, "max_mb": MAX_IMAGE // (1024 * 1024), "catalog": catalog_info(db, device)}
    try:
        record = _read_device(connection, device.external_device_id)
        identity = _identity(record)
        out.update({"current_version": software_version(record), "product_class": identity.get("product_class")})
        out["files"] = list_files(connection, identity.get("product_class"))
    except (nbi.AcsDiagnosticError, FirmwareError) as exc:
        out["files_error"] = str(exc)
    return out


@router.get("/devices/{device_id}/acs/firmware")
def device_acs_firmware(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device, connection = nbi._linked(db, device_id)
        if refresh_upgrade(db, device, connection):
            db.commit()
        return _payload(db, device, connection, core.has_permission(user, "devices.write"))


@router.post("/devices/{device_id}/acs/firmware/files", dependencies=[Depends(require_csrf)])
async def device_acs_firmware_upload(request: Request, device_id: uuid.UUID, image: UploadFile = File(...), version: str = Form("")):
    """Store a vendor image in GenieACS for this CPE's model (nothing is sent to the CPE yet)."""
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device, connection = nbi._linked(db, device_id)
        user_id, external_id, customer_id = user.id, device.external_device_id, device.customer_id
    version = re.sub(r"[\x00-\x1f]", "", version or "").strip()[:120]
    digest = hashlib.sha256()
    size = 0
    temp = tempfile.NamedTemporaryFile(prefix="nsm-fw-", suffix=".bin", delete=False)
    try:
        with temp:
            while chunk := await image.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_IMAGE:
                    raise HTTPException(413, {"code": "too_large", "message": f"Il file supera {MAX_IMAGE // (1024 * 1024)} MB."})
                digest.update(chunk)
                temp.write(chunk)
        if size < MIN_IMAGE:
            raise HTTPException(422, {"code": "too_small", "message": "Il file è troppo piccolo per essere un firmware."})

        def store() -> tuple[str, dict]:
            identity = _identity(nbi._read(connection, external_id, ["_deviceId"]))
            name = file_name_for(identity, image.filename or "", digest.hexdigest())
            store_file(connection, identity, name, temp.name, version or None)
            return name, identity

        try:
            name, identity = await run_in_threadpool(store)
        except (nbi.AcsDiagnosticError, FirmwareError) as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
    finally:
        os.unlink(temp.name)
    with SessionLocal() as db:
        core.add_event(db, "ACS_FIRMWARE_FILE_STORED", actor=db.get(User, user_id), customer_id=customer_id, device_id=device_id, source="portal",
                       details={"file": name, "size": size, "sha256": digest.hexdigest(), "version": version or None,
                                "product_class": identity.get("product_class")})
        db.commit()
    return {"file_name": name, "size": size, "sha256": digest.hexdigest()}


@router.post("/devices/{device_id}/acs/firmware/fetch", dependencies=[Depends(require_csrf)])
def device_acs_firmware_fetch(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    """Download a catalog release from TP-Link's official site and store its image in GenieACS (nothing is sent to the CPE yet)."""
    from app import tplink_firmware

    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device, connection = nbi._linked(db, device_id)
        version = str((body or {}).get("version") or "").strip()[:80]
        if not version:
            raise HTTPException(422, {"code": "no_version", "message": "Indica la versione da scaricare."})
        try:
            result = tplink_firmware.store_release(db, device, connection, version, actor=user)
        except tplink_firmware.TplinkError as exc:
            raise HTTPException(422, {"code": "vendor_site", "message": str(exc)}) from None
        except (nbi.AcsDiagnosticError, FirmwareError) as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        db.commit()
        return result


@router.post("/devices/{device_id}/acs/firmware", dependencies=[Depends(require_csrf)])
def device_acs_firmware_run(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device, connection = nbi._linked(db, device_id)
        if str((body or {}).get("confirm") or "").strip() != _confirm_name(device):
            raise HTTPException(422, {"code": "confirm", "message": "Scrivi il nome dell'apparato per confermare l'aggiornamento."})
        file_name = str((body or {}).get("file_name") or "").strip()[:255]
        expected = re.sub(r"[\x00-\x1f]", "", str((body or {}).get("expected_version") or "")).strip()[:120] or None
        if not file_name:
            raise HTTPException(422, {"code": "no_file", "message": "Scegli il file firmware."})
        try:
            request_upgrade(db, user, device, connection, file_name, expected)
        except (nbi.AcsDiagnosticError, FirmwareError) as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        db.commit()
        return _payload(db, device, connection, True)


@router.get("/admin/acs/firmware-auto")
def admin_acs_firmware_auto(request: Request):
    from app import tplink_firmware

    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        connection = acs._connection(db)
        config = tplink_firmware.auto_settings(connection)
        return {"linked": connection is not None, "enabled": config["enabled"], "fetched": len(config["fetched"])}


@router.post("/admin/acs/firmware-auto", dependencies=[Depends(require_csrf)])
def admin_acs_firmware_auto_save(request: Request, body: dict = Body(default={})):
    from app import tplink_firmware

    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        connection = acs._connection(db)
        if connection is None:
            raise HTTPException(409, {"code": "not_connected", "message": "Collega prima GenieACS (Integrazioni → GenieACS)."})
        config = tplink_firmware.auto_settings(connection)
        enabled = bool((body or {}).get("enabled"))
        stored = dict(connection.settings or {})
        stored["firmware_auto"] = {"enabled": enabled, "fetched": config["fetched"]}
        connection.settings = stored
        core.add_event(db, "ACS_FIRMWARE_AUTO_CHANGED", actor=user, source="portal", details={"enabled": enabled})
        db.commit()
        return {"linked": True, "enabled": enabled, "fetched": len(config["fetched"])}


def install_acs_firmware(app) -> None:
    app.include_router(router)
