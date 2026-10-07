"""Read-only GenieACS NBI connector (ACS-01 / ACS-02 / ACS-03).

GenieACS is the TR-069 ACS preferred by the product requirements; NSM never
implements an ACS itself.  The connector only reads the NBI `/devices`
endpoint: connectivity test, lookup by serial number or MAC with a preview,
explicit association that keeps the NSM Customer/Site, and refresh of the
identity and firmware observed by the ACS.
"""

import ipaddress
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from app import main as core
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import Device, utcnow
from app.secret_vault import decrypt_text, encrypt_text
from app.security import validate_csrf

router = APIRouter()
GENIEACS_PROVIDER = "genieacs"
GENIEACS_TIMEOUT_SECONDS = 12.0
GENIEACS_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
GENIEACS_ONLINE_WINDOW_MINUTES = 1440

DEFAULT_MAC_PARAMETER_PATHS = (
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.MACAddress",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.MACAddress",
    "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.MACAddress",
    "Device.Ethernet.Interface.1.MACAddress",
    "InternetGatewayDevice.LANDevice.1.LANEthernetInterfaceConfig.1.MACAddress",
)
MODEL_PATHS = (
    "Device.DeviceInfo.ModelName",
    "InternetGatewayDevice.DeviceInfo.ModelName",
)
SOFTWARE_PATHS = (
    "Device.DeviceInfo.SoftwareVersion",
    "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
)
HARDWARE_PATHS = (
    "Device.DeviceInfo.HardwareVersion",
    "InternetGatewayDevice.DeviceInfo.HardwareVersion",
)
IP_PATHS = (
    "Device.IP.Interface.1.IPv4Address.1.IPAddress",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ExternalIPAddress",
)
BASE_PROJECTION = (
    "_id",
    "_deviceId",
    "_lastInform",
    *MODEL_PATHS,
    *SOFTWARE_PATHS,
    *HARDWARE_PATHS,
    *IP_PATHS,
)


class GenieAcsConnectorError(RuntimeError):
    pass


def normalize_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Inserisci un URL NBI GenieACS valido con http:// o https://.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("L'URL GenieACS non deve contenere credenziali, query o fragment.")
    host = parsed.hostname.lower()
    if host == "localhost":
        raise ValueError("localhost non è consentito: usa un hostname o IP raggiungibile dal container NSM.")
    try:
        address = ipaddress.ip_address(host)
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
            raise ValueError("L'indirizzo GenieACS indicato non è consentito.")
    except ValueError as exc:
        if "non è consentito" in str(exc):
            raise
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/devices"):
        path = path[:-8]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _auth_payload(mode: str, username: str = "", secret: str = "") -> dict:
    mode = str(mode or "none").strip().casefold()
    if mode not in {"none", "basic", "bearer"}:
        raise ValueError("Modalità autenticazione GenieACS non valida.")
    if mode == "none":
        return {"mode": "none"}
    if mode == "basic":
        username = str(username or "").strip()
        if not username or not secret:
            raise ValueError("HTTP Basic richiede username e password.")
        return {"mode": "basic", "username": username[:200], "secret": str(secret)}
    if not secret:
        raise ValueError("Bearer richiede un token.")
    return {"mode": "bearer", "secret": str(secret)}


def _load_auth(connection: ConnectorIntegration) -> dict:
    try:
        data = json.loads(decrypt_text(connection.secret_encrypted))
    except (ValueError, json.JSONDecodeError) as exc:
        raise GenieAcsConnectorError("Impossibile decifrare le credenziali GenieACS configurate.") from exc
    if not isinstance(data, dict) or data.get("mode") not in {"none", "basic", "bearer"}:
        raise GenieAcsConnectorError("Configurazione autenticazione GenieACS non valida.")
    return data


def _updated_auth(connection, mode: str, username: str, secret: str) -> dict:
    mode = str(mode or "none").strip().casefold()
    old = None
    if connection:
        try:
            old = _load_auth(connection)
        except GenieAcsConnectorError:
            old = None
    if mode == "none":
        return _auth_payload("none")
    if mode == "basic":
        user = str(username or "").strip() or str((old or {}).get("username") or "")
        password = str(secret or "") or (str((old or {}).get("secret") or "") if (old or {}).get("mode") == "basic" else "")
        return _auth_payload("basic", user, password)
    token = str(secret or "") or (str((old or {}).get("secret") or "") if (old or {}).get("mode") == "bearer" else "")
    return _auth_payload("bearer", "", token)


def _http_get(connection: ConnectorIntegration, *, query: dict, projection: tuple[str, ...] | list[str]):
    auth_config = _load_auth(connection)
    headers = {"Accept": "application/json"}
    auth = None
    if auth_config["mode"] == "basic":
        auth = httpx.BasicAuth(auth_config["username"], auth_config["secret"])
    elif auth_config["mode"] == "bearer":
        headers["Authorization"] = f"Bearer {auth_config['secret']}"
    params = {
        "query": json.dumps(query, separators=(",", ":"), ensure_ascii=True),
        "projection": ",".join(dict.fromkeys(projection)),
    }
    try:
        with httpx.Client(
            timeout=GENIEACS_TIMEOUT_SECONDS,
            verify=bool(connection.verify_tls),
            follow_redirects=False,
            headers=headers,
            auth=auth,
        ) as client:
            response = client.get(f"{connection.base_url.rstrip('/')}/devices", params=params)
    except httpx.TimeoutException as exc:
        raise GenieAcsConnectorError("Timeout durante la connessione alla NBI GenieACS.") from exc
    except httpx.HTTPError as exc:
        raise GenieAcsConnectorError("Connessione HTTPS/HTTP alla NBI GenieACS non riuscita.") from exc
    if 300 <= response.status_code < 400:
        raise GenieAcsConnectorError("GenieACS ha risposto con un redirect: configura l'URL NBI finale.")
    if response.status_code in {401, 403}:
        raise GenieAcsConnectorError("Autenticazione NBI rifiutata dal reverse proxy o dal gateway.")
    if response.status_code != 200:
        raise GenieAcsConnectorError(f"GenieACS NBI ha risposto con HTTP {response.status_code}.")
    if len(response.content) > GENIEACS_MAX_RESPONSE_BYTES:
        raise GenieAcsConnectorError("Risposta GenieACS troppo grande per essere elaborata in sicurezza.")
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise GenieAcsConnectorError("GenieACS ha restituito una risposta JSON non valida.") from exc
    if not isinstance(payload, list):
        raise GenieAcsConnectorError("Formato della risposta /devices GenieACS non riconosciuto.")
    return [row for row in payload if isinstance(row, dict)]


def _nested(data: dict, path: str):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    if isinstance(current, dict) and "_value" in current:
        return current.get("_value")
    return current


def _first_value(row: dict, paths):
    for path in paths:
        value = _nested(row, path)
        if value not in {None, ""} and not isinstance(value, (dict, list)):
            return value
    return None


def _normalize_mac_quiet(value):
    try:
        return core.norm_mac(str(value or "").strip())
    except (ValueError, TypeError):
        return None


def _parse_seen(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(text)
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result
    except ValueError:
        return None


def _status(last_seen, online_window_minutes: int):
    if not last_seen:
        return "unknown"
    now = utcnow()
    try:
        age_seconds = (now - last_seen).total_seconds()
    except TypeError:
        return "unknown"
    return "online" if age_seconds <= max(5, online_window_minutes) * 60 else "offline"


def _mac_paths(connection: ConnectorIntegration) -> tuple[str, ...]:
    configured = (connection.settings or {}).get("mac_parameter_paths")
    if isinstance(configured, list):
        clean = tuple(str(item).strip() for item in configured if str(item).strip())
        if clean:
            return clean[:20]
    return DEFAULT_MAC_PARAMETER_PATHS


def candidate_from_genieacs(row: dict, connection: ConnectorIntegration, matched_by: str) -> dict:
    device_id = row.get("_deviceId") if isinstance(row.get("_deviceId"), dict) else {}
    serial = device_id.get("_SerialNumber")
    manufacturer = device_id.get("_Manufacturer")
    product_class = device_id.get("_ProductClass")
    mac_paths = _mac_paths(connection)
    observed_mac = _normalize_mac_quiet(_first_value(row, mac_paths))
    model = _first_value(row, MODEL_PATHS) or product_class
    firmware = _first_value(row, SOFTWARE_PATHS)
    hardware = _first_value(row, HARDWARE_PATHS)
    management_ip = _first_value(row, IP_PATHS)
    last_seen = _parse_seen(row.get("_lastInform"))
    online_window = int((connection.settings or {}).get("online_window_minutes") or GENIEACS_ONLINE_WINDOW_MINUTES)
    identity_parts = [part for part in (manufacturer, model, serial) if part]
    return {
        "external_id": str(row.get("_id") or "").strip() or None,
        "serial_number": str(serial).strip()[:150] if serial else None,
        "primary_mac": observed_mac,
        "manufacturer": str(manufacturer).strip()[:150] if manufacturer else None,
        "product_class": str(product_class).strip()[:150] if product_class else None,
        "device_identity": " · ".join(str(part).strip() for part in identity_parts)[:200] if identity_parts else None,
        "model": str(model).strip()[:150] if model else None,
        "firmware_version": str(firmware).strip()[:150] if firmware else None,
        "hardware_version": str(hardware).strip()[:150] if hardware else None,
        "management_ip": str(management_ip).strip()[:255] if management_ip else None,
        "last_seen": last_seen,
        "status": _status(last_seen, online_window),
        "matched_by": matched_by,
    }


def _connection(db, enabled_only=False):
    row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == GENIEACS_PROVIDER))
    if enabled_only and (not row or not row.is_enabled):
        raise GenieAcsConnectorError("Il connettore GenieACS non è configurato o è disabilitato.")
    return row


def _projection(connection):
    return tuple(dict.fromkeys((*BASE_PROJECTION, *_mac_paths(connection))))


def _query_serial(connection, serial: str):
    rows = _http_get(
        connection,
        query={"_deviceId._SerialNumber": str(serial).strip()},
        projection=_projection(connection),
    )
    return [candidate_from_genieacs(row, connection, "serial") for row in rows]


def _query_mac(connection, mac: str):
    wanted = core.norm_mac(mac)
    alternatives = []
    for path in _mac_paths(connection):
        alternatives.append({path: {"$in": [wanted, wanted.lower()]}})
    rows = _http_get(
        connection,
        query={"$or": alternatives},
        projection=_projection(connection),
    )
    candidates = [candidate_from_genieacs(row, connection, "mac") for row in rows]
    return [candidate for candidate in candidates if candidate.get("primary_mac") == wanted]


def lookup_device(connection: ConnectorIntegration, device: Device) -> dict:
    matches = []
    if device.serial_number:
        matches = _query_serial(connection, device.serial_number)
    if not matches and device.primary_mac:
        matches = _query_mac(connection, device.primary_mac)
    if not matches:
        key = device.serial_number or device.primary_mac or "identificativo disponibile"
        raise GenieAcsConnectorError(f"Nessun CPE GenieACS trovato per {key}.")
    unique = {candidate.get("external_id"): candidate for candidate in matches if candidate.get("external_id")}
    matches = list(unique.values())
    if len(matches) != 1:
        raise GenieAcsConnectorError("GenieACS ha restituito più CPE compatibili: associazione ambigua.")
    candidate = matches[0]
    if not candidate.get("external_id"):
        raise GenieAcsConnectorError("Il CPE GenieACS non espone un identificativo stabile.")
    if device.serial_number and candidate.get("serial_number") and candidate["serial_number"] != device.serial_number:
        raise GenieAcsConnectorError("Il seriale osservato da GenieACS non coincide con il record NSM.")
    if device.primary_mac and candidate.get("primary_mac") and candidate["primary_mac"] != core.norm_mac(device.primary_mac):
        raise GenieAcsConnectorError("Il MAC osservato da GenieACS non coincide con il record NSM.")
    return candidate


def lookup_by_external_id(connection: ConnectorIntegration, external_id: str) -> dict:
    rows = _http_get(
        connection,
        query={"_id": str(external_id)},
        projection=_projection(connection),
    )
    if len(rows) != 1:
        raise GenieAcsConnectorError("Il CPE collegato non è più univocamente disponibile in GenieACS.")
    candidate = candidate_from_genieacs(rows[0], connection, "external_id")
    if candidate.get("external_id") != external_id:
        raise GenieAcsConnectorError("GenieACS ha restituito un identificativo diverso da quello associato.")
    return candidate


def _snapshot(device: Device):
    return {
        "external_device_id": device.external_device_id,
        "device_identity": device.device_identity,
        "model": device.model,
        "serial_number": device.serial_number,
        "primary_mac": device.primary_mac,
        "management_ip": device.management_ip,
        "firmware_version": device.firmware_version,
        "status": device.status,
    }


def apply_candidate(db, device: Device, candidate: dict, actor, event_type="GENIEACS_DEVICE_ASSOCIATED"):
    if device.vendor != "tp-link":
        raise HTTPException(400, "Il connector GenieACS è disponibile per i CPE TP-Link/TR-069.")
    duplicate = db.scalar(
        select(Device.id).where(
            Device.external_device_id == candidate["external_id"],
            Device.management_source == "tr069",
            Device.id != device.id,
        )
    )
    if duplicate:
        raise HTTPException(409, "Questo CPE GenieACS è già associato a un altro record NSM.")
    if device.serial_number and candidate.get("serial_number") and candidate["serial_number"] != device.serial_number:
        raise HTTPException(409, "Il seriale GenieACS non coincide con il record NSM.")
    if device.primary_mac and candidate.get("primary_mac") and candidate["primary_mac"] != core.norm_mac(device.primary_mac):
        raise HTTPException(409, "Il MAC GenieACS non coincide con il record NSM.")
    if not device.primary_mac and candidate.get("primary_mac") and db.scalar(
        select(Device.id).where(Device.vendor == device.vendor, Device.primary_mac == candidate["primary_mac"], Device.id != device.id)
    ):
        raise HTTPException(409, "Il MAC osservato da GenieACS appartiene già a un altro apparato NSM.")

    before = _snapshot(device)
    device.external_device_id = candidate["external_id"]
    device.device_identity = candidate.get("device_identity") or device.device_identity
    device.model = candidate.get("model") or device.model
    device.serial_number = candidate.get("serial_number") or device.serial_number
    device.primary_mac = candidate.get("primary_mac") or device.primary_mac
    device.firmware_version = candidate.get("firmware_version") or device.firmware_version
    device.management_ip = candidate.get("management_ip") or device.management_ip
    device.status = candidate.get("status") or device.status
    device.last_seen = candidate.get("last_seen") or device.last_seen
    device.management_source = "tr069"
    device.inventory_source = "genieacs"
    device.inventory_last_verified_at = utcnow()
    inventory = dict(device.inventory_data or {})
    inventory["tr069"] = {
        "acs": "genieacs",
        "device_id": candidate.get("external_id"),
        "manufacturer": candidate.get("manufacturer"),
        "product_class": candidate.get("product_class"),
        "hardware_version": candidate.get("hardware_version"),
        "last_inform": candidate.get("last_seen").isoformat() if candidate.get("last_seen") else None,
        "matched_by": candidate.get("matched_by"),
        "last_sync_at": utcnow().isoformat(),
    }
    device.inventory_data = inventory
    after = _snapshot(device)
    changes = {key: {"before": before.get(key), "after": after.get(key)} for key in after if before.get(key) != after.get(key)}
    core.add_event(
        db,
        event_type,
        actor=actor,
        customer_id=device.customer_id,
        device_id=device.id,
        details={
            "genieacs_device_id": candidate.get("external_id"),
            "matched_by": candidate.get("matched_by"),
            "changes": changes,
        },
        source="genieacs",
    )
    return changes


def _auth_label(connection):
    if not connection:
        return "—"
    try:
        mode = _load_auth(connection).get("mode", "none")
    except GenieAcsConnectorError:
        return "Configurazione non valida"
    return {"none": "Nessuna / rete privata", "basic": "HTTP Basic", "bearer": "Bearer token"}.get(mode, mode)


def _admin_render(request, db, user, *, message=None, error=None):
    connection = _connection(db)
    auth_mode = "none"
    auth_username = ""
    if connection:
        try:
            auth_data = _load_auth(connection)
            auth_mode = auth_data.get("mode", "none")
            auth_username = auth_data.get("username", "")
        except GenieAcsConnectorError:
            pass
    return core.render(
        request,
        db,
        user,
        "admin_genieacs.html",
        connection=connection,
        auth_mode=auth_mode,
        auth_username=auth_username,
        auth_label=_auth_label(connection),
        mac_paths="\n".join(_mac_paths(connection)) if connection else "\n".join(DEFAULT_MAC_PARAMETER_PATHS),
        message=message,
        error=error,
    )


@router.get("/admin/integrations/genieacs", response_class=HTMLResponse, name="admin_genieacs")
def admin_genieacs(request: Request):
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        return _admin_render(request, db, user)


@router.post("/admin/integrations/genieacs", response_class=HTMLResponse, name="admin_genieacs_save")
def admin_genieacs_save(
    request: Request,
    base_url: str = Form(...),
    auth_mode: str = Form("none"),
    auth_username: str = Form(""),
    auth_secret: str = Form(""),
    mac_parameter_paths: str = Form(""),
    online_window_minutes: int = Form(GENIEACS_ONLINE_WINDOW_MINUTES),
    is_enabled: str | None = Form(None),
    verify_tls: str | None = Form(None),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    try:
        normalized_url = normalize_base_url(base_url)
    except ValueError as exc:
        with SessionLocal() as db:
            user = core.require_admin(request, db)
            return _admin_render(request, db, user, error=str(exc))
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        row = _connection(db)
        try:
            auth_data = _updated_auth(row, auth_mode, auth_username, auth_secret)
        except (ValueError, GenieAcsConnectorError) as exc:
            return _admin_render(request, db, user, error=str(exc))
        paths = []
        for line in str(mac_parameter_paths or "").splitlines():
            path = line.strip()
            if path and path not in paths:
                paths.append(path[:500])
        if not paths:
            paths = list(DEFAULT_MAC_PARAMETER_PATHS)
        window = max(5, min(int(online_window_minutes), 10080))
        settings = {
            "mode": "read_only_nbi",
            "mac_parameter_paths": paths[:20],
            "online_window_minutes": window,
        }
        encrypted = encrypt_text(json.dumps(auth_data, separators=(",", ":")))
        if row:
            row.base_url = normalized_url
            row.secret_encrypted = encrypted
            row.is_enabled = is_enabled is not None
            row.verify_tls = verify_tls is not None
            row.settings = settings
        else:
            row = ConnectorIntegration(
                provider=GENIEACS_PROVIDER,
                name="GenieACS / TR-069",
                base_url=normalized_url,
                secret_encrypted=encrypted,
                is_enabled=is_enabled is not None,
                verify_tls=verify_tls is not None,
                settings=settings,
            )
            db.add(row)
        core.add_event(
            db,
            "GENIEACS_CONNECTOR_CONFIGURED",
            actor=user,
            details={
                "base_url": normalized_url,
                "enabled": row.is_enabled,
                "verify_tls": row.verify_tls,
                "auth_mode": auth_data["mode"],
                "mac_path_count": len(paths[:20]),
                "online_window_minutes": window,
                "mode": "read_only_nbi",
            },
            source="portal",
        )
        db.commit()
        return _admin_render(request, db, user, message="Configurazione GenieACS salvata. Le eventuali credenziali sono cifrate.")


@router.post("/admin/integrations/genieacs/test", response_class=HTMLResponse, name="admin_genieacs_test")
def admin_genieacs_test(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        row = _connection(db)
        if not row:
            return _admin_render(request, db, user, error="Configura prima il connettore GenieACS.")
        try:
            _http_get(
                row,
                query={"_id": "__nsm_connector_test_nonexistent__"},
                projection=("_id",),
            )
            row.last_test_status = "success"
            row.last_error = None
            message = "Connessione GenieACS NBI riuscita in modalità read-only."
            result = "success"
        except GenieAcsConnectorError as exc:
            row.last_test_status = "failed"
            row.last_error = str(exc)[:500]
            message = None
            result = "failed"
        row.last_tested_at = utcnow()
        core.add_event(
            db,
            "GENIEACS_CONNECTOR_TESTED",
            actor=user,
            details={"result": result, "base_url": row.base_url},
            source="genieacs",
            result=result,
            severity="warning" if result == "failed" else "info",
        )
        db.commit()
        return _admin_render(request, db, user, message=message, error=row.last_error if result == "failed" else None)


def _device(db, device_id):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Apparato non trovato.")
    if device.vendor != "tp-link":
        raise HTTPException(400, "Questa operazione è disponibile per CPE TP-Link/TR-069.")
    if not (device.serial_number or device.primary_mac):
        raise HTTPException(409, "Il CPE non ha seriale né MAC per il matching GenieACS.")
    return device


def _device_render(request, db, user, device, *, candidate=None, message=None, error=None):
    return core.render(
        request,
        db,
        user,
        "genieacs_device_link.html",
        device=device,
        connection=_connection(db),
        candidate=candidate,
        message=message,
        error=error,
    )


@router.get("/devices/{device_id}/genieacs", response_class=HTMLResponse, name="genieacs_device_link")
def genieacs_device_link(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        return _device_render(request, db, user, _device(db, device_id))


@router.post("/devices/{device_id}/genieacs/preview", response_class=HTMLResponse, name="genieacs_device_preview")
def genieacs_device_preview(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.read")
        device = _device(db, device_id)
        try:
            candidate = lookup_device(_connection(db, enabled_only=True), device)
            return _device_render(request, db, user, device, candidate=candidate)
        except (GenieAcsConnectorError, ValueError) as exc:
            return _device_render(request, db, user, device, error=str(exc))


@router.post("/devices/{device_id}/genieacs/associate", response_class=HTMLResponse, name="genieacs_device_associate")
def genieacs_device_associate(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        device = _device(db, device_id)
        try:
            connection = _connection(db, enabled_only=True)
            candidate = lookup_device(connection, device)
            apply_candidate(db, device, candidate, user, "GENIEACS_DEVICE_ASSOCIATED")
            connection.last_sync_at = utcnow()
            db.commit()
        except (GenieAcsConnectorError, ValueError) as exc:
            return _device_render(request, db, user, device, error=str(exc))
    return RedirectResponse(f"/devices/{device_id}/genieacs?status=associated", status_code=303)


@router.post("/devices/{device_id}/genieacs/refresh", response_class=HTMLResponse, name="genieacs_device_refresh")
def genieacs_device_refresh(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        device = _device(db, device_id)
        try:
            connection = _connection(db, enabled_only=True)
            if not device.external_device_id:
                raise GenieAcsConnectorError("Il CPE non è ancora associato a un ID GenieACS stabile.")
            candidate = lookup_by_external_id(connection, device.external_device_id)
            if device.serial_number and candidate.get("serial_number") and candidate["serial_number"] != device.serial_number:
                raise GenieAcsConnectorError("Il seriale dell'ID GenieACS collegato è cambiato: refresh bloccato.")
            apply_candidate(db, device, candidate, user, "GENIEACS_INVENTORY_REFRESHED")
            connection.last_sync_at = utcnow()
            db.commit()
        except (GenieAcsConnectorError, ValueError) as exc:
            return _device_render(request, db, user, device, error=str(exc))
    return RedirectResponse(f"/devices/{device_id}/genieacs?status=refreshed", status_code=303)


def summary() -> dict:
    """Template helper for the integrations hub."""
    with SessionLocal() as db:
        connection = _connection(db)
        linked = db.scalar(select(func.count(Device.id)).where(Device.inventory_source == "genieacs", Device.external_device_id.is_not(None)))
        pending = db.scalar(select(func.count(Device.id)).where(Device.vendor == "tp-link", Device.external_device_id.is_(None)))
        return {"connection": connection, "linked": int(linked or 0), "pending": int(pending or 0)}


def install_genieacs_connector(app):
    app.include_router(router)
    core.templates.env.globals["genieacs_summary"] = summary
