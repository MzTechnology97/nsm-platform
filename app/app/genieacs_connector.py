"""Read-only GenieACS NBI connector (ACS-01 / ACS-02 / ACS-03).

GenieACS is the TR-069 ACS preferred by the product requirements; NSM never
implements an ACS itself.  The connector only reads the NBI `/devices`
endpoint: connectivity test, lookup by serial number or MAC with a preview,
explicit association that keeps the NSM Customer/Site, and refresh of the
identity and firmware observed by the ACS.
"""

import ipaddress
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app import main as core
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import Device, utcnow
from app.secret_vault import decrypt_text, encrypt_text

router = APIRouter()
GENIEACS_PROVIDER = "genieacs"
# Generic devices that commonly speak TR-069 (switches, APs and OLTs do not get the ACS tab).
TR069_DEVICE_TYPES = {"router", "ont", "wireless_cpe", "other"}
GENIEACS_TIMEOUT_SECONDS = 12.0
GENIEACS_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
GENIEACS_ONLINE_WINDOW_MINUTES = 1440

# "*" matches every instance (Device.Ethernet.Interface.1, .2, …): CPEs number their objects freely.
DEFAULT_MAC_PARAMETER_PATHS = (
    "Device.Ethernet.Interface.*.MACAddress",
    "Device.Ethernet.Link.*.MACAddress",
    "InternetGatewayDevice.WANDevice.*.WANConnectionDevice.*.WANIPConnection.*.MACAddress",
    "InternetGatewayDevice.WANDevice.*.WANConnectionDevice.*.WANPPPConnection.*.MACAddress",
    "InternetGatewayDevice.WANDevice.*.WANEthernetInterfaceConfig.MACAddress",
    "InternetGatewayDevice.LANDevice.*.LANEthernetInterfaceConfig.*.MACAddress",
)
# Defaults saved on connectors before Core 0.49.201: replaced by the current defaults when read.
LEGACY_MAC_PARAMETER_PATHS = (
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.MACAddress",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.MACAddress",
    "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.MACAddress",
    "Device.Ethernet.Interface.1.MACAddress",
    "InternetGatewayDevice.LANDevice.1.LANEthernetInterfaceConfig.1.MACAddress",
)
# Searching by MAC, GenieACS has no wildcard: each path is expanded to at most this many concrete
# instances (8 for one "*", 2×2 for two, 2×2×2 for three) and queried on its own, keeping the URL short.
MAC_QUERY_MAX_PATHS = 8
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
# The address the CPE gives the ACS for connection requests comes first: it is the one the ACS reaches it on.
CONNECTION_REQUEST_URL_PATHS = (
    "Device.ManagementServer.ConnectionRequestURL",
    "InternetGatewayDevice.ManagementServer.ConnectionRequestURL",
)
IP_PATHS = (
    "Device.IP.Interface.*.IPv4Address.*.IPAddress",
    "InternetGatewayDevice.WANDevice.*.WANConnectionDevice.*.WANPPPConnection.*.ExternalIPAddress",
    "InternetGatewayDevice.WANDevice.*.WANConnectionDevice.*.WANIPConnection.*.ExternalIPAddress",
)
BASE_PROJECTION = (
    "_id",
    "_deviceId",
    "_lastInform",
    *MODEL_PATHS,
    *SOFTWARE_PATHS,
    *HARDWARE_PATHS,
    *CONNECTION_REQUEST_URL_PATHS,
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


def _instance_order(key: str):
    return (0, int(key)) if key.isdigit() else (1, key)


def _values(data: dict, path: str) -> list:
    """Every value at ``path``; a "*" segment walks all instances in numeric order."""
    nodes = [data]
    for part in path.split("."):
        following = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if part == "*":
                following.extend(node[key] for key in sorted((k for k in node if not k.startswith("_")), key=_instance_order))
            elif part in node:
                following.append(node[part])
        nodes = following
    values = []
    for node in nodes:
        value = node.get("_value") if isinstance(node, dict) and "_value" in node else node
        if value not in {None, ""} and not isinstance(value, (dict, list)):
            values.append(value)
    return values


def _first_value(row: dict, paths):
    for path in paths:
        values = _values(row, path)
        if values:
            return values[0]
    return None


GENERIC_PRODUCT_CLASSES = {"device", "internetgatewaydevice"}


def product_class(row: dict) -> str | None:
    """``_ProductClass``, or the model name when the CPE reports only its data model root (``Device`` on an Archer VR1210v)."""
    value = str((row.get("_deviceId") or {}).get("_ProductClass") or "").strip()
    if value.lower() in GENERIC_PRODUCT_CLASSES:
        value = str(_first_value(row, MODEL_PATHS) or "").strip() or value
    return value or None


def _usable_ipv4(value) -> str | None:
    try:
        address = ipaddress.IPv4Address(str(value).strip())
    except ValueError:
        return None
    if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
        return None
    return str(address)


def _management_ip(row: dict) -> str | None:
    for path in CONNECTION_REQUEST_URL_PATHS:
        for url in _values(row, path):
            host = urlsplit(str(url)).hostname
            if host and _usable_ipv4(host):
                return _usable_ipv4(host)
    for path in IP_PATHS:
        for value in _values(row, path):
            if _usable_ipv4(value):
                return _usable_ipv4(value)
    return None


def _macs(row: dict, paths) -> list[str]:
    found = []
    for path in paths:
        for value in _values(row, path):
            mac = _normalize_mac_quiet(value)
            if mac and mac != "00:00:00:00:00:00" and mac not in found:
                found.append(mac)
    return found


def _projection_path(path: str) -> str:
    """GenieACS projects whole objects: a wildcard path is fetched through its parent object."""
    head = path.split(".*", 1)[0]
    return head


def _expand_instances(path: str) -> list[str]:
    stars = path.count("*")
    if not stars:
        return [path]
    per_star = max(1, int(round(MAC_QUERY_MAX_PATHS ** (1 / stars), 6)))
    paths = [path]
    for _ in range(stars):
        paths = [item.replace("*", str(index), 1) for item in paths for index in range(1, per_star + 1)]
    return paths[:MAC_QUERY_MAX_PATHS]


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
        if clean and clean != LEGACY_MAC_PARAMETER_PATHS:
            return clean[:20]
    return DEFAULT_MAC_PARAMETER_PATHS


def candidate_from_genieacs(row: dict, connection: ConnectorIntegration, matched_by: str) -> dict:
    device_id = row.get("_deviceId") if isinstance(row.get("_deviceId"), dict) else {}
    serial = device_id.get("_SerialNumber")
    manufacturer = device_id.get("_Manufacturer")
    model_class = product_class(row)
    macs = _macs(row, _mac_paths(connection))
    model = _first_value(row, MODEL_PATHS) or model_class
    firmware = _first_value(row, SOFTWARE_PATHS)
    hardware = _first_value(row, HARDWARE_PATHS)
    management_ip = _management_ip(row)
    last_seen = _parse_seen(row.get("_lastInform"))
    online_window = int((connection.settings or {}).get("online_window_minutes") or GENIEACS_ONLINE_WINDOW_MINUTES)
    identity_parts = [part for part in (manufacturer, model, serial) if part]
    return {
        "external_id": str(row.get("_id") or "").strip() or None,
        "serial_number": str(serial).strip()[:150] if serial else None,
        "primary_mac": macs[0] if macs else None,
        "macs": macs,
        "manufacturer": str(manufacturer).strip()[:150] if manufacturer else None,
        "product_class": model_class[:150] if model_class else None,
        "device_identity": " · ".join(str(part).strip() for part in identity_parts)[:200] if identity_parts else None,
        "model": str(model).strip()[:150] if model else None,
        "firmware_version": str(firmware).strip()[:150] if firmware else None,
        "hardware_version": str(hardware).strip()[:150] if hardware else None,
        "management_ip": str(management_ip).strip()[:255] if management_ip else None,
        "last_seen": last_seen,
        "status": _status(last_seen, online_window),
        "matched_by": matched_by,
    }


def auto_link() -> bool:
    """Link NSM to the integrated GenieACS when it is enabled and nothing is configured yet.

    An external GenieACS configured by the operator is never replaced.  Runs in
    its own session so callers' transactions are not committed by accident.
    """
    url = internal_nbi_url()
    if not url:
        return False
    with SessionLocal() as db:
        if db.scalar(select(ConnectorIntegration.id).where(ConnectorIntegration.provider == GENIEACS_PROVIDER)):
            return False
        settings = {"mode": "read_only_nbi", "integrated": True, "auto": True,
                    "mac_parameter_paths": list(DEFAULT_MAC_PARAMETER_PATHS), "online_window_minutes": GENIEACS_ONLINE_WINDOW_MINUTES}
        db.add(ConnectorIntegration(provider=GENIEACS_PROVIDER, name="GenieACS / TR-069 (integrato)", base_url=url, is_enabled=True, verify_tls=True,
                                    secret_encrypted=encrypt_text(json.dumps(_auth_payload("none"), separators=(",", ":"))), settings=settings))
        core.add_event(db, "GENIEACS_CONNECTOR_CONFIGURED", details={"base_url": url, "integrated": True, "auto": True}, source="system")
        try:
            db.commit()
        except Exception:  # noqa: BLE001 - concurrent first request created it
            db.rollback()
            return False
    return True


def _connection(db, enabled_only=False):
    row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == GENIEACS_PROVIDER))
    if row is None and auto_link():
        row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == GENIEACS_PROVIDER))
    if enabled_only and (not row or not row.is_enabled):
        raise GenieAcsConnectorError("Il connettore GenieACS non è configurato o è disabilitato.")
    return row


def _projection(connection):
    return tuple(dict.fromkeys(_projection_path(path) for path in (*BASE_PROJECTION, *_mac_paths(connection))))


def _query_serial(connection, serial: str):
    rows = _http_get(
        connection,
        query={"_deviceId._SerialNumber": str(serial).strip()},
        projection=_projection(connection),
    )
    return [candidate_from_genieacs(row, connection, "serial") for row in rows]


def _query_mac(connection, mac: str):
    wanted = core.norm_mac(mac)
    rows = {}
    for path in _mac_paths(connection):
        alternatives = [{concrete: {"$in": [wanted, wanted.lower()]}} for concrete in _expand_instances(path)]
        for row in _http_get(connection, query={"$or": alternatives}, projection=_projection(connection)):
            rows.setdefault(str(row.get("_id")), row)
    candidates = [candidate_from_genieacs(row, connection, "mac") for row in rows.values()]
    return [candidate for candidate in candidates if wanted in candidate.get("macs", [])]


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
    if not _mac_matches(device, candidate):
        raise GenieAcsConnectorError("Il MAC osservato da GenieACS non coincide con il record NSM.")
    return candidate


def _mac_matches(device: Device, candidate: dict) -> bool:
    """The MAC on the NSM record is one of the CPE's interfaces (LAN, WAN, …), or one side is unknown."""
    macs = candidate.get("macs") or ([candidate["primary_mac"]] if candidate.get("primary_mac") else [])
    return not device.primary_mac or not macs or core.norm_mac(device.primary_mac) in macs


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


def tr069_capable(device) -> bool:
    """Devices that can be managed through the ACS: TR-069 CPEs of any brand (TP-Link, Huawei, ZTE, …)."""
    if device.vendor == "tp-link" or device.management_source in ("tr069", "acs") or device.inventory_source == GENIEACS_PROVIDER:
        return True
    return device.vendor == "generic" and str(device.device_type or "") in TR069_DEVICE_TYPES


def apply_candidate(db, device: Device, candidate: dict, actor, event_type="GENIEACS_DEVICE_ASSOCIATED"):
    if not tr069_capable(device):
        raise HTTPException(400, "Il connector GenieACS è disponibile per i CPE TR-069 (TP-Link o altri produttori).")
    duplicate = db.scalar(
        select(Device.id).where(
            Device.external_device_id == candidate["external_id"],
            Device.management_source == "tr069",
            Device.id != device.id,
        ).execution_options(include_decommissioned=True)
    )
    if duplicate:
        raise HTTPException(409, "Questo CPE GenieACS è già associato a un altro record NSM.")
    if device.serial_number and candidate.get("serial_number") and candidate["serial_number"] != device.serial_number:
        raise HTTPException(409, "Il seriale GenieACS non coincide con il record NSM.")
    if not _mac_matches(device, candidate):
        raise HTTPException(409, "Il MAC GenieACS non coincide con il record NSM.")
    if not device.primary_mac and candidate.get("primary_mac") and db.scalar(
        select(Device.id).where(Device.vendor == device.vendor, Device.primary_mac == candidate["primary_mac"], Device.id != device.id)
        .execution_options(include_decommissioned=True)
    ):
        raise HTTPException(409, "Il MAC osservato da GenieACS appartiene già a un altro apparato NSM.")

    before = _snapshot(device)
    device.external_device_id = candidate["external_id"]
    device.device_identity = candidate.get("device_identity") or device.device_identity
    device.model = candidate.get("model") or device.model
    device.serial_number = candidate.get("serial_number") or device.serial_number
    device.primary_mac = device.primary_mac or candidate.get("primary_mac")
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


def auth_info(connection) -> tuple[str, str]:
    """(auth mode, username) of the stored NBI credentials, without the secret."""
    if connection:
        try:
            data = _load_auth(connection)
            return data.get("mode", "none"), data.get("username", "")
        except GenieAcsConnectorError:
            pass
    return "none", ""


def save_config(db, user, *, base_url: str, auth_mode: str, auth_username: str, auth_secret: str, mac_parameter_paths: str,
                online_window_minutes, enabled: bool, verify_tls: bool) -> ConnectorIntegration:
    """Store an external NBI (credentials encrypted, kept when empty), audited.

    Raises ``ValueError`` or ``GenieAcsConnectorError`` with the reason. Caller commits.
    """
    normalized_url = normalize_base_url(base_url)
    row = _connection(db)
    auth_data = _updated_auth(row, auth_mode, auth_username, auth_secret)
    paths = []
    for line in str(mac_parameter_paths or "").splitlines():
        path = line.strip()
        if path and path not in paths:
            paths.append(path[:500])
    if not paths:
        paths = list(DEFAULT_MAC_PARAMETER_PATHS)
    try:
        window = max(5, min(int(online_window_minutes), 10080))
    except (TypeError, ValueError):
        window = GENIEACS_ONLINE_WINDOW_MINUTES
    settings = {
        "mode": "read_only_nbi",
        "mac_parameter_paths": paths[:20],
        "online_window_minutes": window,
    }
    encrypted = encrypt_text(json.dumps(auth_data, separators=(",", ":")))
    if row:
        row.base_url = normalized_url
        row.secret_encrypted = encrypted
        row.is_enabled = enabled
        row.verify_tls = verify_tls
        row.settings = settings
    else:
        row = ConnectorIntegration(
            provider=GENIEACS_PROVIDER,
            name="GenieACS / TR-069",
            base_url=normalized_url,
            secret_encrypted=encrypted,
            is_enabled=enabled,
            verify_tls=verify_tls,
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
    return row


def use_internal(db, user) -> ConnectorIntegration:
    """Point the connector at the integrated GenieACS (internal network, no authentication), audited.

    ValueError when the integrated GenieACS is not running. Caller commits.
    """
    url = internal_nbi_url()
    if not url:
        raise ValueError("GenieACS integrato non attivo: esegui ./manage.sh acs-enable sul server.")
    row = _connection(db)
    settings = dict(row.settings or {}) if row else {}
    settings.update({"mode": "read_only_nbi", "integrated": True})
    settings.setdefault("mac_parameter_paths", list(DEFAULT_MAC_PARAMETER_PATHS))
    settings.setdefault("online_window_minutes", GENIEACS_ONLINE_WINDOW_MINUTES)
    encrypted = encrypt_text(json.dumps(_auth_payload("none"), separators=(",", ":")))
    if row:
        row.base_url, row.secret_encrypted, row.is_enabled, row.settings = url, encrypted, True, settings
    else:
        row = ConnectorIntegration(provider=GENIEACS_PROVIDER, name="GenieACS / TR-069 (integrato)", base_url=url,
                                   secret_encrypted=encrypted, is_enabled=True, verify_tls=True, settings=settings)
        db.add(row)
    core.add_event(db, "GENIEACS_CONNECTOR_CONFIGURED", actor=user,
                   details={"base_url": url, "enabled": True, "auth_mode": "none", "integrated": True, "mode": "read_only_nbi"}, source="portal")
    return row


def test_connection(db, user, row) -> tuple[bool, str]:
    """Read /devices once with a filter matching nothing; outcome stored and audited. Returns (ok, message). Caller commits."""
    try:
        _http_get(row, query={"_id": "__nsm_connector_test_nonexistent__"}, projection=("_id",))
        row.last_test_status, row.last_error = "success", None
        ok, message, result = True, "Connessione GenieACS NBI riuscita in modalità read-only.", "success"
    except GenieAcsConnectorError as exc:
        row.last_test_status, row.last_error = "failed", str(exc)[:500]
        ok, message, result = False, row.last_error, "failed"
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
    return ok, message


def internal_nbi_url() -> str | None:
    """NBI URL of the GenieACS shipped in the NSM Docker stack (``./manage.sh acs-enable``)."""
    value = os.getenv("GENIEACS_INTERNAL_NBI_URL", "").strip()
    try:
        return normalize_base_url(value) if value else None
    except ValueError:
        return None


def associate_record(db, user, device: Device) -> None:
    """Link a TR-069 CPE to its GenieACS record by serial (then MAC) and import identity and firmware (audited); commits."""
    connection = _connection(db, enabled_only=True)
    candidate = lookup_device(connection, device)
    apply_candidate(db, device, candidate, user, "GENIEACS_DEVICE_ASSOCIATED")
    connection.last_sync_at = utcnow()
    db.commit()


def refresh_record(db, user, device: Device) -> None:
    """Re-read the linked GenieACS record by its id (blocked if the serial changed); commits."""
    connection = _connection(db, enabled_only=True)
    if not device.external_device_id:
        raise GenieAcsConnectorError("Il CPE non è ancora associato a un ID GenieACS stabile.")
    candidate = lookup_by_external_id(connection, device.external_device_id)
    if device.serial_number and candidate.get("serial_number") and candidate["serial_number"] != device.serial_number:
        raise GenieAcsConnectorError("Il seriale dell'ID GenieACS collegato è cambiato: refresh bloccato.")
    apply_candidate(db, device, candidate, user, "GENIEACS_INVENTORY_REFRESHED")
    connection.last_sync_at = utcnow()
    db.commit()


def summary() -> dict:
    """Template helper for the integrations hub."""
    with SessionLocal() as db:
        connection = _connection(db)
        linked = db.scalar(select(func.count(Device.id)).where(Device.inventory_source == "genieacs", Device.external_device_id.is_not(None)))
        pending = db.scalar(select(func.count(Device.id)).where(Device.vendor == "tp-link", Device.external_device_id.is_(None)))
        return {"connection": connection, "linked": int(linked or 0), "pending": int(pending or 0)}


def install_genieacs_connector(app):
    app.include_router(router)
