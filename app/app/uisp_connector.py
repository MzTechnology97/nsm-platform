"""Read-only UISP Network connector for Core 0.26."""

import ipaddress
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app import main as core
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import Device, utcnow
from app import uisp_metrics
from app import uisp_firmware
from app.secret_vault import decrypt_text, encrypt_text
from app.security import validate_csrf
from app.ui_feedback import exception_message, flash_redirect

router = APIRouter()
UISP_PROVIDER = "uisp"
UISP_API_PATH = "/nms/api/v2.1/devices"
UISP_TIMEOUT_SECONDS = 12.0
UISP_MAX_RESPONSE_BYTES = 20 * 1024 * 1024
SYNC_INTERVAL_DEFAULT = 15
SYNC_INTERVAL_MIN = 5
SYNC_INTERVAL_MAX = 1440


class UispConnectorError(RuntimeError):
    pass


def normalize_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Inserisci un URL UISP valido con http:// o https://.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("L'URL UISP non deve contenere credenziali, query o fragment.")
    host = parsed.hostname.lower()
    if host == "localhost":
        raise ValueError("localhost non è consentito come endpoint UISP.")
    try:
        address = ipaddress.ip_address(host)
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
            raise ValueError("L'indirizzo UISP indicato non è consentito.")
    except ValueError as exc:
        if "non è consentito" in str(exc):
            raise
    path = (parsed.path or "").rstrip("/")
    for suffix in ("/nms/api/v2.1", "/nms/api-docs"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _parse_sync_interval(value) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        minutes = int(text)
    except ValueError:
        raise ValueError("Intervallo di sincronizzazione non valido.")
    if not SYNC_INTERVAL_MIN <= minutes <= SYNC_INTERVAL_MAX:
        raise ValueError(
            f"L'intervallo di sincronizzazione deve essere tra {SYNC_INTERVAL_MIN} e {SYNC_INTERVAL_MAX} minuti."
        )
    return minutes


def _error_chain(exc: BaseException) -> str:
    parts, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        parts.append(f"{type(exc).__name__}: {exc}")
        exc = exc.__cause__ or exc.__context__
    return " | ".join(parts).lower()


def describe_http_error(exc: BaseException, url: str, verify_tls: bool) -> str:
    """Operator-facing reason for a failed UISP request."""
    target = urlsplit(url)
    where = f"{target.hostname}:{target.port or (443 if target.scheme == 'https' else 80)}"
    text = _error_chain(exc)
    if isinstance(exc, httpx.TimeoutException):
        return f"Timeout durante la connessione a UISP ({where}): verifica indirizzo, porta e firewall tra NSM e UISP."
    if "certificate" in text or "ssl" in text or "tls" in text:
        if verify_tls:
            return (
                f"Certificato TLS di UISP non valido per {target.hostname} (self-signed, scaduto o emesso per un altro nome). "
                "Usa l'URL con il nome DNS del certificato oppure, per un UISP raggiungibile solo in rete locale, "
                "disattiva «Verifica certificato TLS»."
            )
        return f"Negoziazione TLS con UISP ({where}) non riuscita: verifica che la porta indicata sia HTTPS."
    if "name or service not known" in text or "getaddrinfo" in text or "nodename nor servname" in text or "no address associated" in text:
        return f"Nome host UISP non risolvibile: {target.hostname}."
    if "refused" in text:
        return f"Connessione rifiutata da {where}: UISP non è in ascolto su questo indirizzo/porta."
    if "unreachable" in text or "no route" in text:
        return f"Rete non raggiungibile verso {where}."
    if "remote protocol" in text or "server disconnected" in text:
        return f"UISP ({where}) ha chiuso la connessione: verifica schema (http/https) e porta."
    return f"Connessione HTTPS/HTTP a UISP non riuscita ({where})."


def _http_get(url: str, token: str, verify_tls: bool):
    headers = {"Accept": "application/json", "x-auth-token": token}
    try:
        with httpx.Client(
            timeout=UISP_TIMEOUT_SECONDS,
            verify=bool(verify_tls),
            follow_redirects=False,
            headers=headers,
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise UispConnectorError(describe_http_error(exc, url, verify_tls)) from exc
    if 300 <= response.status_code < 400:
        raise UispConnectorError("UISP ha risposto con un redirect: configura l'URL finale della console.")
    if response.status_code in {401, 403}:
        raise UispConnectorError("Token UISP non autorizzato o privo dei permessi di lettura.")
    if response.status_code != 200:
        raise UispConnectorError(f"UISP ha risposto con HTTP {response.status_code}.")
    if len(response.content) > UISP_MAX_RESPONSE_BYTES:
        raise UispConnectorError("Risposta UISP troppo grande per essere elaborata in sicurezza.")
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise UispConnectorError("UISP ha restituito una risposta JSON non valida.") from exc


def _device_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("devices", "data", "items"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    raise UispConnectorError("Formato della lista dispositivi UISP non riconosciuto.")


def _normalize_mac_quiet(value):
    try:
        return core.norm_mac(str(value or "").strip())
    except (ValueError, TypeError):
        return None


def _macs_in_payload(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in {"mac", "macaddress", "mac_address"}:
                normalized = _normalize_mac_quiet(child)
                if normalized:
                    found.add(normalized)
            elif isinstance(child, (dict, list)):
                found.update(_macs_in_payload(child))
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)):
                found.update(_macs_in_payload(child))
    return found


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
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _status(value) -> str:
    status = str(value or "").strip().casefold()
    if status in {"active", "online", "connected", "reachable", "ready"}:
        return "online"
    if status in {"inactive", "offline", "disconnected", "unreachable", "failed"}:
        return "offline"
    return "unknown"


def candidate_from_uisp(row: dict) -> dict:
    identification = row.get("identification") if isinstance(row.get("identification"), dict) else {}
    overview = row.get("overview") if isinstance(row.get("overview"), dict) else {}
    site = identification.get("site") if isinstance(identification.get("site"), dict) else {}
    external_id = identification.get("id") or row.get("id") or row.get("_id")
    primary_mac = _normalize_mac_quiet(identification.get("mac") or row.get("mac"))
    if not primary_mac:
        macs = sorted(_macs_in_payload(row))
        primary_mac = macs[0] if macs else None
    identity = (
        identification.get("displayName")
        or identification.get("name")
        or identification.get("hostname")
        or row.get("name")
    )
    model = identification.get("modelName") or identification.get("model") or row.get("model")
    serial = identification.get("serialNumber") or row.get("serialNumber")
    firmware = identification.get("firmwareVersion") or row.get("firmwareVersion")
    status_raw = overview.get("status") or row.get("status")
    last_seen_raw = overview.get("lastSeen") or row.get("lastSeen")
    management_ip = row.get("ipAddress") or overview.get("ipAddress") or identification.get("ipAddress")
    return {
        "external_id": str(external_id).strip() if external_id else None,
        "primary_mac": primary_mac,
        "device_identity": str(identity).strip()[:200] if identity else None,
        "model": str(model).strip()[:150] if model else None,
        "serial_number": str(serial).strip()[:150] if serial else None,
        "firmware_version": str(firmware).strip()[:150] if firmware else None,
        "management_ip": str(management_ip).strip()[:255] if management_ip else None,
        "status": _status(status_raw),
        "uisp_status": str(status_raw).strip()[:80] if status_raw is not None else None,
        "last_seen": _parse_seen(last_seen_raw),
        "site": {
            "id": str(site.get("id"))[:255] if site.get("id") else None,
            "name": str(site.get("name"))[:200] if site.get("name") else None,
            "type": str(site.get("type"))[:80] if site.get("type") else None,
        },
        "metrics": uisp_metrics.extract(overview),
        "firmware": uisp_firmware.extract(row),
        "role": str(identification.get("role") or row.get("role") or "")[:80] or None,
        "category": str(identification.get("category") or row.get("category") or "")[:80] or None,
    }


def _connection(db, enabled_only=False):
    row = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == UISP_PROVIDER))
    if enabled_only and (not row or not row.is_enabled):
        raise UispConnectorError("Il connettore UISP non è configurato o è disabilitato.")
    return row


def _fetch_candidates(connection: ConnectorIntegration) -> list[dict]:
    try:
        token = decrypt_text(connection.secret_encrypted)
    except ValueError as exc:
        raise UispConnectorError("Impossibile decifrare il token UISP configurato.") from exc
    payload = _http_get(
        f"{connection.base_url.rstrip('/')}{UISP_API_PATH}",
        token,
        connection.verify_tls,
    )
    return [candidate_from_uisp(row) for row in _device_rows(payload)]


def lookup_by_mac(connection: ConnectorIntegration, mac: str) -> dict:
    wanted = core.norm_mac(mac)
    matches = [candidate for candidate in _fetch_candidates(connection) if candidate.get("primary_mac") == wanted]
    if not matches:
        raise UispConnectorError(f"Nessun dispositivo UISP trovato con MAC {wanted}.")
    if len(matches) > 1:
        raise UispConnectorError(f"UISP ha restituito più dispositivi con MAC {wanted}: associazione ambigua.")
    candidate = matches[0]
    if not candidate.get("external_id"):
        raise UispConnectorError("Il dispositivo UISP trovato non espone un identificativo stabile.")
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


def apply_candidate(db, device: Device, candidate: dict, actor, event_type="UISP_DEVICE_ASSOCIATED"):
    if device.vendor != "ubiquiti":
        raise HTTPException(400, "Il connector UISP può essere associato solo a dispositivi Ubiquiti.")
    wanted = core.norm_mac(device.primary_mac or "")
    if candidate.get("primary_mac") != wanted:
        raise HTTPException(409, "Il MAC del dispositivo UISP non coincide con il record NSM.")
    duplicate = db.scalar(
        select(Device.id).where(
            Device.vendor == "ubiquiti",
            Device.external_device_id == candidate["external_id"],
            Device.id != device.id,
        )
    )
    if duplicate:
        raise HTTPException(409, "Questo dispositivo UISP è già associato a un altro record NSM.")

    before = _snapshot(device)
    device.external_device_id = candidate["external_id"]
    device.device_identity = candidate.get("device_identity") or device.device_identity
    device.model = candidate.get("model") or device.model
    device.serial_number = candidate.get("serial_number") or device.serial_number
    device.firmware_version = candidate.get("firmware_version") or device.firmware_version
    device.management_ip = candidate.get("management_ip") or device.management_ip
    device.status = candidate.get("status") or device.status
    device.last_seen = candidate.get("last_seen") or device.last_seen
    uisp_firmware.apply(device, candidate.get("firmware"), utcnow())
    device.management_source = "uisp"
    device.inventory_source = "uisp"
    device.inventory_last_verified_at = utcnow()
    inventory = dict(device.inventory_data or {})
    inventory["uisp"] = {
        "device_id": candidate.get("external_id"),
        "status": candidate.get("uisp_status"),
        "site": candidate.get("site"),
        "role": candidate.get("role"),
        "category": candidate.get("category"),
        "last_sync_at": utcnow().isoformat(),
    }
    device.inventory_data = inventory
    uisp_metrics.record(db, device, candidate, utcnow())
    after = _snapshot(device)
    changes = {key: {"before": before.get(key), "after": after.get(key)} for key in after if before.get(key) != after.get(key)}
    core.add_event(
        db,
        event_type,
        actor=actor,
        customer_id=device.customer_id,
        device_id=device.id,
        details={
            "uisp_device_id": candidate.get("external_id"),
            "matched_mac": candidate.get("primary_mac"),
            "changes": changes,
            "uisp_site_metadata": candidate.get("site"),
        },
        source="uisp",
    )
    return changes


def _sync_view(connection: ConnectorIntegration | None) -> dict:
    """Sync health for the admin page, with stored ISO timestamps parsed."""
    state = dict((connection.settings or {}).get("sync") or {}) if connection else {}
    for key in ("last_attempt_at", "last_success_at", "next_attempt_at"):
        state[key] = _parse_seen(state.get(key))
    return state


def _admin_render(request, db, user, *, message=None, error=None):
    connection = _connection(db)
    return core.render(
        request,
        db,
        user,
        "admin_uisp.html",
        connection=connection,
        has_secret=bool(connection and connection.secret_encrypted),
        sync=_sync_view(connection),
        sync_interval=int((connection.settings or {}).get("sync_interval_minutes") or SYNC_INTERVAL_DEFAULT)
        if connection
        else SYNC_INTERVAL_DEFAULT,
        message=message,
        error=error,
    )


@router.get("/admin/integrations/uisp", response_class=HTMLResponse, name="admin_uisp")
def admin_uisp(request: Request):
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        return _admin_render(request, db, user)


@router.post("/admin/integrations/uisp", response_class=HTMLResponse, name="admin_uisp_save")
def admin_uisp_save(
    request: Request,
    base_url: str = Form(...),
    api_token: str = Form(""),
    is_enabled: str | None = Form(None),
    verify_tls: str | None = Form(None),
    sync_interval_minutes: str = Form(""),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    try:
        normalized_url = normalize_base_url(base_url)
        interval = _parse_sync_interval(sync_interval_minutes)
    except ValueError as exc:
        with SessionLocal() as db:
            user = core.require_admin(request, db)
            return _admin_render(request, db, user, error=str(exc))
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        row = _connection(db)
        token = str(api_token or "").strip()
        if not row and not token:
            return _admin_render(request, db, user, error="Inserisci un token API UISP read-only.")
        if row:
            row.base_url = normalized_url
            row.is_enabled = is_enabled is not None
            row.verify_tls = verify_tls is not None
            if token:
                row.secret_encrypted = encrypt_text(token)
            if interval is not None:
                row.settings = {**(row.settings or {}), "sync_interval_minutes": interval}
        else:
            row = ConnectorIntegration(
                provider=UISP_PROVIDER,
                name="UISP Network",
                base_url=normalized_url,
                secret_encrypted=encrypt_text(token),
                is_enabled=is_enabled is not None,
                verify_tls=verify_tls is not None,
                settings={
                    "api_version": "v2.1",
                    "mode": "read_only",
                    "sync_interval_minutes": interval or SYNC_INTERVAL_DEFAULT,
                },
            )
            db.add(row)
        core.add_event(
            db,
            "UISP_CONNECTOR_CONFIGURED",
            actor=user,
            details={
                "base_url": normalized_url,
                "enabled": row.is_enabled,
                "verify_tls": row.verify_tls,
                "token_updated": bool(token),
                "mode": "read_only",
                "sync_interval_minutes": (row.settings or {}).get("sync_interval_minutes"),
            },
            source="portal",
        )
        db.commit()
        return _admin_render(request, db, user, message="Configurazione UISP salvata. Il token è memorizzato cifrato.")


@router.post("/admin/integrations/uisp/test", response_class=HTMLResponse, name="admin_uisp_test")
def admin_uisp_test(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        row = _connection(db)
        if not row:
            return _admin_render(request, db, user, error="Configura prima il connettore UISP.")
        try:
            candidates = _fetch_candidates(row)
            row.last_test_status = "success"
            row.last_error = None
            message = f"Connessione UISP riuscita: {len(candidates)} dispositivi leggibili."
            result = "success"
        except UispConnectorError as exc:
            row.last_test_status = "failed"
            row.last_error = str(exc)[:500]
            message = None
            result = "failed"
        row.last_tested_at = utcnow()
        core.add_event(
            db,
            "UISP_CONNECTOR_TESTED",
            actor=user,
            details={"result": result, "base_url": row.base_url},
            source="uisp",
            result=result,
            severity="warning" if result == "failed" else "info",
        )
        db.commit()
        return _admin_render(request, db, user, message=message, error=row.last_error if result == "failed" else None)


def _device_render(request, db, user, device, *, candidate=None, message=None, error=None):
    connection = _connection(db)
    return core.render(
        request,
        db,
        user,
        "uisp_device_link.html",
        device=device,
        connection=connection,
        candidate=candidate,
        message=message,
        error=error,
        uisp_view=uisp_metrics.view(db, device) if device.external_device_id else None,
        format_metric=uisp_metrics.format_value,
    )


def _uisp_device(db, device_id):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Apparato non trovato.")
    if device.vendor != "ubiquiti":
        raise HTTPException(400, "Questa operazione è disponibile solo per dispositivi Ubiquiti.")
    if not device.primary_mac:
        raise HTTPException(409, "Il dispositivo Ubiquiti non ha un MAC associato.")
    return device


def _device_http_feedback(request: Request, device_id: uuid.UUID, exc: HTTPException):
    if exc.status_code == 404:
        return flash_redirect(
            request,
            "/devices",
            "error",
            exception_message(exc, "Apparato non trovato."),
            title="Apparato non trovato",
        )
    if exc.status_code == 403:
        return flash_redirect(
            request,
            f"/devices/{device_id}",
            "error",
            exception_message(exc, "Non hai i permessi necessari per questa operazione."),
            title="Operazione non autorizzata",
        )
    if exc.status_code in {400, 409}:
        return flash_redirect(
            request,
            f"/devices/{device_id}/uisp",
            "warning",
            exception_message(exc, "Operazione UISP non disponibile."),
            title="Operazione UISP non disponibile",
        )
    raise exc


@router.get("/devices/{device_id}/uisp", response_class=HTMLResponse, name="uisp_device_link")
def uisp_device_link(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        device = _uisp_device(db, device_id)
        return _device_render(request, db, user, device)


@router.get("/api/v1/devices/{device_id}/uisp-metrics", name="uisp_device_metrics")
def uisp_device_metrics(request: Request, device_id: uuid.UUID, range: str = "24h"):
    from app import uisp_metrics

    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            raise HTTPException(401)
        if not core.has_permission(user, "monitoring.read"):
            raise HTTPException(403)
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        try:
            return uisp_metrics.series(db, device, range)
        except ValueError:
            raise HTTPException(400, "Intervallo non valido.") from None


@router.post("/devices/{device_id}/uisp/preview", response_class=HTMLResponse, name="uisp_device_preview")
def uisp_device_preview(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
    try:
        validate_csrf(request, csrf)
        with SessionLocal() as db:
            user = core.require_permission(request, db, "devices.read")
            device = _uisp_device(db, device_id)
            try:
                connection = _connection(db, enabled_only=True)
                candidate = lookup_by_mac(connection, device.primary_mac)
                return _device_render(request, db, user, device, candidate=candidate)
            except (UispConnectorError, ValueError) as exc:
                return _device_render(request, db, user, device, error=str(exc))
    except HTTPException as exc:
        return _device_http_feedback(request, device_id, exc)


@router.post("/devices/{device_id}/uisp/associate", response_class=HTMLResponse, name="uisp_device_associate")
def uisp_device_associate(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
    return_to = f"/devices/{device_id}/uisp"
    try:
        validate_csrf(request, csrf)
        with SessionLocal() as db:
            user = core.require_permission(request, db, "devices.write")
            device = _uisp_device(db, device_id)
            try:
                connection = _connection(db, enabled_only=True)
                candidate = lookup_by_mac(connection, device.primary_mac)
                apply_candidate(db, device, candidate, user, "UISP_DEVICE_ASSOCIATED")
                connection.last_sync_at = utcnow()
                db.commit()
            except (UispConnectorError, ValueError) as exc:
                return flash_redirect(
                    request,
                    return_to,
                    "warning",
                    str(exc),
                    title="Associazione UISP non completata",
                )
    except HTTPException as exc:
        return _device_http_feedback(request, device_id, exc)
    return flash_redirect(
        request,
        return_to,
        "success",
        "Dispositivo associato a UISP e inventario aggiornato.",
        title="Associazione UISP completata",
    )


@router.post("/devices/{device_id}/uisp/refresh", response_class=HTMLResponse, name="uisp_device_refresh")
def uisp_device_refresh(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
    return_to = f"/devices/{device_id}/uisp"
    try:
        validate_csrf(request, csrf)
        with SessionLocal() as db:
            user = core.require_permission(request, db, "devices.write")
            device = _uisp_device(db, device_id)
            try:
                connection = _connection(db, enabled_only=True)
                candidate = lookup_by_mac(connection, device.primary_mac)
                if device.external_device_id and candidate["external_id"] != device.external_device_id:
                    raise UispConnectorError("Il MAC ora corrisponde a un ID UISP diverso: refresh bloccato per sicurezza.")
                apply_candidate(db, device, candidate, user, "UISP_INVENTORY_REFRESHED")
                connection.last_sync_at = utcnow()
                db.commit()
            except (UispConnectorError, ValueError) as exc:
                return flash_redirect(
                    request,
                    return_to,
                    "warning",
                    str(exc),
                    title="Refresh UISP non completato",
                )
    except HTTPException as exc:
        return _device_http_feedback(request, device_id, exc)
    return flash_redirect(
        request,
        return_to,
        "success",
        "Inventario UISP aggiornato mantenendo invariati cliente e sito NSM.",
        title="Refresh UISP completato",
    )


def install_uisp_connector(app):
    app.include_router(router)
