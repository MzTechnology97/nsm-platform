"""ACS-06: download and upload speed test run by a TR-069 CPE (TR-143) through GenieACS.

The CPE itself downloads (``DownloadDiagnostics``) or uploads
(``UploadDiagnostics``) a test file over HTTP and reports bytes and times, so
the result is the speed of the customer's line up to the test server, not of
the Wi-Fi behind it.

- Test server: by default NSM itself. The CPE reaches it at the host it uses
  for the ACS (``ManagementServer.URL``) on HTTP port 80, or at the address set
  in *Amministrazione → ACS* (``settings["speedtest"]`` of the GenieACS
  connector). Each test gets its own signed URL
  (``/acs-speedtest/{device}/{expires}/{size}/{signature}/nsm-speedtest.bin``),
  valid for ``LINK_VALID`` and for that size only; at most ``MAX_TRANSFERS``
  transfers run at once. Download streams ``size`` bytes, upload (PUT or POST)
  reads and discards them. The operator can point both tests at other servers
  instead (custom download and upload URLs).
- ``POST /api/ui/v1/devices/{id}/acs/speedtest/{download|upload}``
  (``devices.write``): one ``setParameterValues`` task with a connection
  request, one test at a time per CPE. Audited ``ACS_SPEEDTEST_REQUESTED``.
- ``GET /api/ui/v1/devices/{id}/acs/speedtest`` (``devices.read``): follows the
  running test like ping and traceroute (``acs_diagnostics``) and stores the
  result in ``inventory_data["acs_speedtest"]``: Mbps from the CPE's
  test bytes over ``EOMTime - BOMTime``. Audited ``ACS_SPEEDTEST_COMPLETED``.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app import acs_profiles as profiles
from app import acs_diagnostics as nbi
from app import genieacs_connector as acs
from app import main as core
from app.config import settings as app_settings
from app.db import SessionLocal
from app.models import Device, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
public = APIRouter(include_in_schema=False)
KINDS = ("download", "upload")
DEFAULT_SIZE_MB, MIN_SIZE_MB, MAX_SIZE_MB = 25, 1, 200
LINK_VALID = timedelta(minutes=15)
GIVE_UP = timedelta(minutes=10)
MAX_TRANSFERS = 4
MIN_VALID_BYTES = 1024 * 1024
FILENAME = "nsm-speedtest.bin"
_CHUNK = os.urandom(64 * 1024)  # random bytes: nothing on the way can compress them
_transfers = 0

OBJECTS = {model: {kind: profiles.path(model, kind) for kind in KINDS} for model in ("tr181", "tr098")}
ACS_URL = {model: profiles.path(model, "acs_url") for model in ("tr181", "tr098")}


# --- Settings ----------------------------------------------------------------------------------

def speedtest_settings(connection) -> dict:
    stored = ((connection.settings or {}).get("speedtest") or {}) if connection else {}
    return {"server_url": stored.get("server_url") or "", "download_url": stored.get("download_url") or "",
            "upload_url": stored.get("upload_url") or "", "size_mb": int(stored.get("size_mb") or DEFAULT_SIZE_MB)}


def _http_url(value, label: str, base_only: bool = False) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or any(ch in value for ch in " \n\r\t"):
        raise HTTPException(422, {"code": "invalid", "message": f"{label}: indirizzo http:// o https:// non valido."})
    if base_only and parts.path not in ("", "/"):
        raise HTTPException(422, {"code": "invalid", "message": f"{label}: solo schema, nome e porta (senza percorso)."})
    return value.rstrip("/") if base_only else value


def clean_settings(body: dict) -> dict:
    try:
        size = int(body.get("size_mb") or DEFAULT_SIZE_MB)
    except (TypeError, ValueError):
        size = 0
    if not MIN_SIZE_MB <= size <= MAX_SIZE_MB:
        raise HTTPException(422, {"code": "invalid", "message": f"Dimensione del test tra {MIN_SIZE_MB} e {MAX_SIZE_MB} MB."})
    return {"server_url": _http_url(body.get("server_url"), "Indirizzo di NSM per i CPE", base_only=True),
            "download_url": _http_url(body.get("download_url"), "URL di download"),
            "upload_url": _http_url(body.get("upload_url"), "URL di upload"), "size_mb": size}


# --- Signed links to NSM -----------------------------------------------------------------------

def _signature(device_id: str, expires: int, size: int) -> str:
    message = f"acs-speedtest|{device_id}|{expires}|{size}".encode("utf-8")
    return hmac.new(app_settings.app_secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]


def signed_path(device_id, size: int, now: datetime) -> str:
    expires = int((now + LINK_VALID).timestamp())
    return f"/acs-speedtest/{device_id}/{expires}/{size}/{_signature(str(device_id), expires, size)}/{FILENAME}"


def _check_link(device_id: str, expires: int, size: int, signature: str) -> None:
    if not hmac.compare_digest(signature, _signature(device_id, expires, size)) or expires < time.time() or not 0 < size <= MAX_SIZE_MB * 1024 * 1024:
        raise HTTPException(404)


def _acs_host(record: dict, model: str) -> str | None:
    """Host the CPE uses to reach the ACS: on an all-in-one NSM server it is NSM too."""
    host = urlsplit(str(nbi._value(record, ACS_URL[model]) or "")).hostname
    return f"[{host}]" if host and ":" in host else host


def test_url(device, kind: str, config: dict, record: dict, model: str, now: datetime) -> str:
    custom = config["download_url" if kind == "download" else "upload_url"]
    if custom:
        return custom
    base = config["server_url"]
    if not base:
        host = _acs_host(record, model)
        if not host:
            raise HTTPException(409, {"code": "no_server", "message": "Non so a quale indirizzo il CPE raggiunge NSM: impostalo in Amministrazione → ACS → Test di velocità."})
        base = f"http://{host}"
    return base + signed_path(device.id, config["size_mb"] * 1024 * 1024, now)


# --- Results -----------------------------------------------------------------------------------

def _time(value) -> datetime | None:
    text = str(value or "").strip()
    if not text or text.startswith("0001"):
        return None
    text = re.sub(r"(\.\d{6})\d+", r"\1", text.replace("Z", "+00:00"))
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _bytes(record: dict, base: str, names: tuple[str, ...], high32: str) -> int | None:
    for name in names:
        value = nbi._number(nbi._value(record, f"{base}.{name}"))
        if value:
            high = nbi._number(nbi._value(record, f"{base}.{high32}")) if name.startswith("Total") else None
            return value + (high << 32 if high else 0)
    return None


def _begin(record: dict, base: str, end: datetime | None) -> datetime | None:
    """Start of the transfer: BOMTime, unless it is not before EOMTime or not after the request (the VX230v
    reports BOMTime an hour off), then the TCP open response, then the request time."""
    request = _time(nbi._value(record, f"{base}.ROMTime"))
    for name in ("BOMTime", "TCPOpenResponseTime", "ROMTime"):
        stamp = _time(nbi._value(record, f"{base}.{name}"))
        if stamp and end and stamp < end and (request is None or stamp >= request):
            return stamp
    return None


def parse_result(model: str, kind: str, record: dict) -> dict:
    base = OBJECTS[model][kind]
    state = str(nbi._value(record, f"{base}.DiagnosticsState") or "")
    end = _time(nbi._value(record, f"{base}.EOMTime"))
    begin = _begin(record, base, end)
    if kind == "download":
        moved = _bytes(record, base, ("TestBytesReceived", "TotalBytesReceived"), "X_TP_TotalBytesReceivedHigh32")
    else:
        moved = _bytes(record, base, ("TestBytesSent", "TotalBytesSent", "TestFileLength"), "X_TP_TotalBytesSentHigh32")
    seconds = (end - begin).total_seconds() if begin and end else None
    # A server that redirects or refuses the CPE (403 without a browser User-Agent) "completes" with a few hundred bytes.
    short = bool(moved is not None and moved < MIN_VALID_BYTES and state.lower() in ("complete", "completed"))
    mbps = round(moved * 8 / seconds / 1_000_000, 1) if moved and seconds and seconds > 0 and not short else None
    return {"state": state, "bytes": moved, "seconds": round(seconds, 2) if seconds else None, "mbps": mbps, "short": short,
            "began_at": begin.isoformat() if begin else None}


def detect_model(connection, external_id: str) -> str | None:
    record = nbi._read(connection, external_id, [OBJECTS["tr181"]["download"], OBJECTS["tr098"]["download"]])
    for model, objects in OBJECTS.items():
        if nbi._node(record, objects["download"]):
            return model
    return None


# --- Records -----------------------------------------------------------------------------------

def _store(device, kind: str, entry: dict, model: str | None = None) -> None:
    data = dict(device.inventory_data or {})
    tests = dict(data.get("acs_speedtest") or {})
    tests[kind] = entry
    if model:
        tests["model"] = model
    data["acs_speedtest"] = tests
    device.inventory_data = data


def request_record(db, user, device, connection, kind: str, now: datetime | None = None) -> dict:
    """Ask the CPE to run one speed test (audited); returns the stored entry. Caller commits."""
    now = now or utcnow()
    if kind not in KINDS:
        raise HTTPException(404)
    tests = (device.inventory_data or {}).get("acs_speedtest") or {}
    running = next((k for k in KINDS if (tests.get(k) or {}).get("state") == "requested"), None)
    if running:
        raise HTTPException(409, {"code": "running", "message": "C'è già un test di velocità in corso su questo CPE: attendi che finisca."})
    model = tests.get("model") or detect_model(connection, device.external_device_id)
    if model is None:
        raise HTTPException(409, {"code": "unsupported", "message": "Il CPE non espone il test di velocità TR-143 (DownloadDiagnostics/UploadDiagnostics)."})
    config = speedtest_settings(connection)
    base = OBJECTS[model][kind]
    record = nbi._read(connection, device.external_device_id, [ACS_URL[model], f"{base}.ProtocolVersion"])
    url = test_url(device, kind, config, record, model, now)
    size = config["size_mb"] * 1024 * 1024
    if kind == "download":
        values = [[f"{base}.DownloadURL", url, "xsd:string"]]
    else:
        values = [[f"{base}.UploadURL", url, "xsd:string"], [f"{base}.TestFileLength", size, "xsd:unsignedInt"]]
    # With "Any" the VX230v tries IPv6 first and fails without IPv6 on the WAN (Error_InitConnectionFailed).
    protocol = nbi._node(record, f"{base}.ProtocolVersion")
    if isinstance(protocol, dict) and protocol.get("_writable") and ":" not in (urlsplit(url).hostname or ""):
        values.append([f"{base}.ProtocolVersion", "IPv4", "xsd:string"])
    values.append([f"{base}.DiagnosticsState", "Requested", "xsd:string"])
    started = nbi._task(connection, device.external_device_id, {"name": "setParameterValues", "parameterValues": values})
    server = "custom" if config["download_url" if kind == "download" else "upload_url"] else "nsm"
    entry = {"state": "requested", "server": server, "host": urlsplit(url).hostname, "size_mb": config["size_mb"], "requested_at": now.isoformat(),
             "by": user.username if user else None, "started": started, "checked_at": None, "result": None}
    _store(device, kind, entry, model)
    core.add_event(db, "ACS_SPEEDTEST_REQUESTED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"kind": kind, "server": server, "size_mb": config["size_mb"], "started": started})
    return entry


def refresh_record(db, device, connection, now: datetime | None = None) -> bool:
    """Follow the running speed test of one device; True when something changed. Caller commits."""
    now = now or utcnow()
    tests = (device.inventory_data or {}).get("acs_speedtest") or {}
    model = tests.get("model")
    changed = False
    for kind in KINDS:
        entry = dict(tests.get(kind) or {})
        if entry.get("state") != "requested" or model not in OBJECTS:
            continue
        checked = datetime.fromisoformat(entry["checked_at"]) if entry.get("checked_at") else None
        if checked and now - checked < nbi.REFRESH_EVERY:
            continue
        base = OBJECTS[model][kind]
        entry["checked_at"] = now.isoformat()
        try:
            nbi._task(connection, device.external_device_id, {"name": "refreshObject", "objectName": base})
            result = parse_result(model, kind, nbi._read(connection, device.external_device_id, [base]))
        except nbi.AcsDiagnosticError as exc:
            entry["error"] = str(exc)
            result = None
        state = (result or {}).get("state", "")
        finished = state.lower() in ("complete", "completed")
        if finished or state.startswith("Error"):
            entry.update({"state": "complete" if finished else "error", "result": result, "completed_at": now.isoformat(), "error": None})
            core.add_event(db, "ACS_SPEEDTEST_COMPLETED", customer_id=device.customer_id, device_id=device.id, source="worker",
                           details={"kind": kind, "state": state, "mbps": (result or {}).get("mbps")})
        elif now - datetime.fromisoformat(entry["requested_at"]) > GIVE_UP:
            entry.update({"state": "timeout", "completed_at": now.isoformat()})
        _store(device, kind, entry)
        changed = True
    return changed


def _payload(device, connection, can_run: bool) -> dict:
    tests = (device.inventory_data or {}).get("acs_speedtest") or {}
    config = speedtest_settings(connection)
    return {"linked": bool(device.external_device_id and connection and connection.is_enabled), "can_run": can_run, "model": tests.get("model"),
            "size_mb": config["size_mb"], "custom": {"download": bool(config["download_url"]), "upload": bool(config["upload_url"])},
            "tests": {kind: tests.get(kind) for kind in KINDS}}


# --- Routes ------------------------------------------------------------------------------------

@router.get("/devices/{device_id}/acs/speedtest")
def device_acs_speedtest(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        device = db.get(Device, device_id)
        if device is None or not acs.tr069_capable(device):
            raise HTTPException(404)
        connection = acs._connection(db)
        if device.external_device_id and connection is not None and connection.is_enabled and refresh_record(db, device, connection):
            db.commit()
        return _payload(device, connection, core.has_permission(user, "devices.write"))


@router.post("/devices/{device_id}/acs/speedtest/{kind}", dependencies=[Depends(require_csrf)])
def device_acs_speedtest_run(request: Request, device_id: uuid.UUID, kind: str):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device, connection = nbi._linked(db, device_id)
        try:
            request_record(db, user, device, connection, kind)
        except nbi.AcsDiagnosticError as exc:
            raise HTTPException(422, {"code": "genieacs", "message": str(exc)}) from None
        db.commit()
        return _payload(device, connection, True)


@router.get("/admin/acs/speedtest")
def admin_acs_speedtest(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        connection = acs._connection(db)
        return {"linked": connection is not None, **speedtest_settings(connection), "min_mb": MIN_SIZE_MB, "max_mb": MAX_SIZE_MB}


@router.post("/admin/acs/speedtest", dependencies=[Depends(require_csrf)])
def admin_acs_speedtest_save(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        connection = acs._connection(db)
        if connection is None:
            raise HTTPException(409, {"code": "not_connected", "message": "Collega prima GenieACS (Integrazioni → GenieACS)."})
        cleaned = clean_settings(body or {})
        stored = dict(connection.settings or {})
        stored["speedtest"] = cleaned
        connection.settings = stored
        core.add_event(db, "ACS_SPEEDTEST_SETTINGS_CHANGED", actor=user, source="portal",
                       details={"custom_download": bool(cleaned["download_url"]), "custom_upload": bool(cleaned["upload_url"]),
                                "server_url": bool(cleaned["server_url"]), "size_mb": cleaned["size_mb"]})
        db.commit()
        return {"linked": True, **cleaned, "min_mb": MIN_SIZE_MB, "max_mb": MAX_SIZE_MB}


def _take_slot() -> bool:
    """At most MAX_TRANSFERS speed-test transfers at once."""
    global _transfers
    if _transfers >= MAX_TRANSFERS:
        return False
    _transfers += 1
    return True


def _free_slot() -> None:
    global _transfers
    _transfers = max(0, _transfers - 1)


@public.get("/acs-speedtest/{device_id}/{expires}/{size}/{signature}/{name}")
def speedtest_download(device_id: str, expires: int, size: int, signature: str, name: str):
    _check_link(device_id, expires, size, signature)

    async def stream():
        # The slot is taken when the transfer starts, so an abandoned response holds none.
        if not _take_slot():
            return
        try:
            left = size
            while left > 0:
                piece = _CHUNK if left >= len(_CHUNK) else _CHUNK[:left]
                left -= len(piece)
                yield piece
                await asyncio.sleep(0)
        finally:
            _free_slot()

    return StreamingResponse(stream(), media_type="application/octet-stream",
                             headers={"Content-Length": str(size), "Cache-Control": "no-store"})


@public.api_route("/acs-speedtest/{device_id}/{expires}/{size}/{signature}/{name}", methods=["PUT", "POST"])
async def speedtest_upload(request: Request, device_id: str, expires: int, size: int, signature: str, name: str):
    _check_link(device_id, expires, size, signature)
    if not _take_slot():
        raise HTTPException(503)
    try:
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > size + 1024 * 1024:
                return Response(status_code=413)
    finally:
        _free_slot()
    return Response(status_code=200)


def install_acs_speedtest(app) -> None:
    app.include_router(router)
    app.include_router(public)
