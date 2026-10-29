"""MikroTik BRAS: PPPoE sessions of the customers read from the concentrator (BRAS-01, operator decision 2026-10-23).

The PPPoE concentrator is a MikroTik that is not managed by NSM (no agent): NSM reads it with a **read-only RouterOS
user**, never changing anything on it.

- Connection (*Integrazioni → BRAS MikroTik*, ``users.manage``): address, read-only username and password (encrypted),
  TLS check. Two transports: the RouterOS 7 **REST** API (``https://host[:port]/rest/ppp/active``) or the RouterOS
  **API** (``api-ssl://host:8729`` or ``api://host:8728``; RouterOS 6.43+ login), for routers without REST.
- Every ``interval_minutes`` (5 by default, and on *Leggi ora*) the worker reads ``/ppp/active`` once and keeps one row
  per username in ``bras_sessions``: online now (address, caller-id, uptime, since when) or offline since when.
  A failed read changes nothing. At most ``MAX_SESSIONS`` sessions are read.
- The sessions feed the existing ISPBilling matching (``ispbilling_radius.ppp_sessions``): caller-id MAC → device
  with that MAC, session address → device with that management IP; several candidates mean no match. Matching runs
  again after every read.
- Shown on the device page (ISPBilling accounts of the device: *Online da …*, IP, MAC) and on the customer page
  (*Connessioni PPPoE*: the customer's RADIUS accounts with their session state).

Audit: ``BRAS_CONFIG_SAVED``, ``BRAS_SYNC_REQUESTED``; the last read is in the connector settings.
"""
from __future__ import annotations

import json
import re
import socket
import ssl
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func, select

from app import main as core
from app.bras_models import BrasSession
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import Customer, Device, utcnow
from app.secret_vault import decrypt_text, encrypt_text
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
PROVIDER = "mikrotik_bras"
TIMEOUT = 30.0
MAX_SESSIONS = 20000
DEFAULT_INTERVAL = 5
TRANSPORT = None  # tests swap in an httpx.MockTransport (REST)
API_CONNECT = None  # tests swap in a fake RouterOS API connection factory
_MAC = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


class BrasError(RuntimeError):
    pass


# --- Values ----------------------------------------------------------------------------------------

def mac_of(value) -> str | None:
    text = str(value or "").strip()
    return text.replace("-", ":").upper() if _MAC.match(text) else None


def uptime_seconds(value) -> int | None:
    """RouterOS uptimes: ``1w2d3h4m5s`` (v7), ``2d03:04:05`` / ``03:04:05`` (v6)."""
    text = str(value or "").strip()
    if not text:
        return None
    total, matched = 0, False
    for amount, unit in re.findall(r"(\d+)([wdhms])", text):
        total += int(amount) * {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
        matched = True
    clock = re.search(r"(\d+):(\d{2}):(\d{2})$", text)
    if clock:
        total += int(clock.group(1)) * 3600 + int(clock.group(2)) * 60 + int(clock.group(3))
        matched = True
    return total if matched else None


# --- Connection ------------------------------------------------------------------------------------

def connection_row(db):
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == PROVIDER))


def _credentials(row) -> tuple[str, str]:
    try:
        secret = json.loads(decrypt_text(row.secret_encrypted))
    except (ValueError, TypeError) as exc:
        raise BrasError("Credenziali del BRAS non leggibili: salvale di nuovo.") from exc
    return str(secret.get("username") or ""), str(secret.get("password") or "")


def normalize_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        raise ValueError("Indica l'indirizzo del BRAS.")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("https", "http", "api-ssl", "api") or not parts.hostname:
        raise ValueError("Indirizzo non valido: usa https://indirizzo (REST, RouterOS 7) oppure api-ssl://indirizzo:8729 / api://indirizzo:8728.")
    return f"{parts.scheme}://{parts.netloc}"


def _rest_sessions(row, username: str, password: str) -> list[dict]:
    url = f"{row.base_url}/rest/ppp/active"
    try:
        with httpx.Client(timeout=TIMEOUT, verify=bool(row.verify_tls), follow_redirects=False, transport=TRANSPORT) as client:
            response = client.get(url, auth=(username, password), headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise BrasError(f"BRAS non raggiungibile ({type(exc).__name__}).") from exc
    if response.status_code in (401, 403):
        raise BrasError("Il BRAS ha rifiutato utente o password (serve un utente RouterOS con policy read e rest-api).")
    if response.status_code == 404:
        raise BrasError("Il BRAS non espone l'API REST (RouterOS 7 con servizio www-ssl): usa api-ssl://indirizzo:8729.")
    if response.status_code != 200:
        raise BrasError(f"Il BRAS ha risposto HTTP {response.status_code}.")
    try:
        rows = response.json()
    except ValueError as exc:
        raise BrasError("Risposta del BRAS non valida.") from exc
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


# RouterOS API (binary protocol), enough for login and one print.

def _encode_length(size: int) -> bytes:
    if size < 0x80:
        return bytes([size])
    if size < 0x4000:
        return (size | 0x8000).to_bytes(2, "big")
    if size < 0x200000:
        return (size | 0xC00000).to_bytes(3, "big")
    if size < 0x10000000:
        return (size | 0xE0000000).to_bytes(4, "big")
    return b"\xf0" + size.to_bytes(4, "big")


class ApiConnection:
    def __init__(self, sock):
        self.sock = sock

    def _recv(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise BrasError("Il BRAS ha chiuso la connessione API.")
            data += chunk
        return data

    def _length(self) -> int:
        first = self._recv(1)[0]
        if first < 0x80:
            return first
        if first < 0xC0:
            return ((first & 0x3F) << 8) + self._recv(1)[0]
        if first < 0xE0:
            return ((first & 0x1F) << 16) + int.from_bytes(self._recv(2), "big")
        if first < 0xF0:
            return ((first & 0x0F) << 24) + int.from_bytes(self._recv(3), "big")
        return int.from_bytes(self._recv(4), "big")

    def send(self, *words: str) -> None:
        payload = b"".join(_encode_length(len(w.encode())) + w.encode() for w in words) + b"\x00"
        self.sock.sendall(payload)

    def sentence(self) -> list[str]:
        words = []
        while True:
            size = self._length()
            if size == 0:
                return words
            words.append(self._recv(size).decode("utf-8", "replace"))

    def talk(self, *words: str) -> list[dict]:
        self.send(*words)
        rows = []
        while True:
            reply = self.sentence()
            if not reply:
                continue
            kind, attrs = reply[0], {}
            for word in reply[1:]:
                if word.startswith("="):
                    key, _, value = word[1:].partition("=")
                    attrs[key] = value
            if kind == "!re":
                rows.append(attrs)
                if len(rows) > MAX_SESSIONS:
                    raise BrasError(f"Il BRAS ha più di {MAX_SESSIONS} sessioni: lettura interrotta.")
            elif kind == "!done":
                return rows
            elif kind in ("!trap", "!fatal"):
                raise BrasError(f"Il BRAS ha rifiutato il comando: {attrs.get('message') or kind}.")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _api_connect(row) -> ApiConnection:
    parts = urlsplit(row.base_url)
    secure = parts.scheme == "api-ssl"
    port = parts.port or (8729 if secure else 8728)
    try:
        sock = socket.create_connection((parts.hostname, port), timeout=TIMEOUT)
        if secure:
            context = ssl.create_default_context()
            if not row.verify_tls:
                context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
            sock = context.wrap_socket(sock, server_hostname=parts.hostname)
    except (OSError, ssl.SSLError) as exc:
        raise BrasError(f"BRAS non raggiungibile sulla porta API {port} ({type(exc).__name__}).") from exc
    return ApiConnection(sock)


def _api_sessions(row, username: str, password: str) -> list[dict]:
    connection = (API_CONNECT or _api_connect)(row)
    try:
        try:
            connection.talk("/login", f"=name={username}", f"=password={password}")
        except BrasError as exc:
            raise BrasError("Il BRAS ha rifiutato utente o password (serve un utente RouterOS con policy read e api).") from exc
        return connection.talk("/ppp/active/print")
    except OSError as exc:
        raise BrasError(f"Lettura API dal BRAS interrotta ({type(exc).__name__}).") from exc
    finally:
        connection.close()


def fetch_sessions(row) -> list[dict]:
    username, password = _credentials(row)
    scheme = urlsplit(row.base_url).scheme
    rows = _api_sessions(row, username, password) if scheme in ("api", "api-ssl") else _rest_sessions(row, username, password)
    if len(rows) > MAX_SESSIONS:
        raise BrasError(f"Il BRAS ha più di {MAX_SESSIONS} sessioni: lettura interrotta.")
    return rows


# --- Sync ------------------------------------------------------------------------------------------

def sync(db, now: datetime | None = None) -> dict:
    """Read ``/ppp/active`` once and update ``bras_sessions``; then match the ISPBilling accounts again. Caller commits."""
    now = now or utcnow()
    row = connection_row(db)
    if row is None or not row.is_enabled:
        return {"status": "disabled"}
    settings = dict(row.settings or {})
    try:
        sessions = fetch_sessions(row)
    except BrasError as exc:
        settings["last_sync"] = {"status": "failed", "at": now.isoformat(), "error": str(exc)[:300]}
        row.settings, row.last_error = settings, str(exc)[:500]
        return {"status": "failed", "error": str(exc)}
    current: dict[str, dict] = {}
    duplicates = 0
    for session in sessions:
        user = str(session.get("name") or "").strip().lower()[:120]
        if not user:
            continue
        if user in current:
            duplicates += 1
            continue
        current[user] = session
    existing = {r.username: r for r in db.scalars(select(BrasSession))}
    came, went = 0, 0
    for user, session in current.items():
        record = existing.get(user)
        if record is None:
            record = BrasSession(id=uuid.uuid4(), username=user)
            db.add(record)
        uptime = uptime_seconds(session.get("uptime"))
        session_id = str(session.get("session-id") or "")[:40] or None
        restarted = not record.online or (session_id and record.session_id and session_id != record.session_id)
        if not record.online:
            came += 1
        caller = str(session.get("caller-id") or "").strip()[:64] or None
        record.online, record.offline_since, record.last_seen_at = True, None, now
        record.service = str(session.get("service") or "")[:20] or None
        record.caller_id, record.mac = caller, mac_of(caller)
        record.address = str(session.get("address") or "").strip()[:64] or None
        record.session_id, record.uptime_seconds = session_id, uptime
        if restarted or record.started_at is None:
            record.started_at = now - timedelta(seconds=uptime) if uptime is not None else now
    for user, record in existing.items():
        if record.online and user not in current:
            record.online, record.offline_since, record.uptime_seconds = False, now, None
            went += 1
    stats = {"status": "success", "at": now.isoformat(), "sessions": len(current), "came_online": came, "went_offline": went, "duplicates": duplicates}
    settings["last_sync"] = stats
    settings.pop("sync_requested", None)
    row.settings, row.last_sync_at, row.last_error = settings, now, None
    db.flush()
    from app.ispbilling_models import IspbillingRadiusAccount

    if db.scalar(select(func.count()).select_from(IspbillingRadiusAccount)):
        from app import ispbilling_radius

        stats["matching"] = ispbilling_radius.match_devices(db)
    return stats


def scheduled_sync(now: datetime | None = None) -> dict:
    """Worker task: read the BRAS every ``interval_minutes`` or when asked."""
    now = now or utcnow()
    with SessionLocal() as db:
        row = connection_row(db)
        if row is None or not row.is_enabled:
            return {"status": "disabled"}
        settings = row.settings or {}
        interval = timedelta(minutes=int(settings.get("interval_minutes") or DEFAULT_INTERVAL))
        if not settings.get("sync_requested") and row.last_sync_at and now - row.last_sync_at < interval:
            return {"status": "not_due"}
        result = sync(db, now)
        db.commit()
        return result


# --- Views -----------------------------------------------------------------------------------------

def session_view(record: BrasSession | None) -> dict | None:
    if record is None:
        return None
    return {"online": record.online, "address": record.address, "mac": record.mac, "caller_id": record.caller_id, "service": record.service,
            "uptime_seconds": record.uptime_seconds, "started_at": record.started_at.isoformat() if record.started_at else None,
            "offline_since": record.offline_since.isoformat() if record.offline_since else None,
            "last_seen_at": record.last_seen_at.isoformat() if record.last_seen_at else None}


def sessions_for(db, usernames) -> dict[str, dict]:
    names = sorted({str(u).lower() for u in usernames if u})
    if not names:
        return {}
    return {r.username: session_view(r) for r in db.scalars(select(BrasSession).where(BrasSession.username.in_(names)))}


def customer_connections(db, customer) -> list[dict]:
    """The customer's ISPBilling RADIUS accounts with their PPPoE session on the BRAS."""
    from app.ispbilling_models import IspbillingCustomer, IspbillingRadiusAccount

    external = [c.external_id for c in db.scalars(select(IspbillingCustomer).where(IspbillingCustomer.nsm_customer_id == customer.id))]
    if not external:
        return []
    accounts = list(db.scalars(select(IspbillingRadiusAccount).where(IspbillingRadiusAccount.customer_external_id.in_(external))
                               .order_by(IspbillingRadiusAccount.username)))
    sessions = sessions_for(db, [a.username for a in accounts])
    devices = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_([a.nsm_device_id for a in accounts if a.nsm_device_id])))}
    out = []
    for account in accounts:
        device = devices.get(account.nsm_device_id)
        out.append({"username": account.username, "profile": account.profile_name, "status": account.status, "static_ip": account.static_ip,
                    "session": sessions.get(account.username.lower()),
                    "device": {"id": str(device.id), "name": device.display_name or device.device_identity or device.name} if device else None,
                    "matched_by": account.matched_by})
    return out


def summary(db) -> dict:
    row = connection_row(db)
    online = int(db.scalar(select(func.count()).select_from(BrasSession).where(BrasSession.online.is_(True))) or 0)
    total = int(db.scalar(select(func.count()).select_from(BrasSession)) or 0)
    return {"connection": row, "online": online, "offline": total - online}


def _payload(db) -> dict:
    row = connection_row(db)
    info = summary(db)
    out = {"configured": row is not None, "enabled": bool(row and row.is_enabled), "url": row.base_url if row else "",
           "verify_tls": bool(row.verify_tls) if row else False, "username": None, "interval_minutes": DEFAULT_INTERVAL,
           "last_sync": None, "last_sync_at": None, "sync_requested": False, "online": info["online"], "offline": info["offline"],
           "last_test_status": row.last_test_status if row else None, "last_error": row.last_error if row else None}
    if row:
        settings = row.settings or {}
        try:
            out["username"] = _credentials(row)[0]
        except BrasError:
            out["username"] = None
        out.update({"interval_minutes": int(settings.get("interval_minutes") or DEFAULT_INTERVAL), "last_sync": settings.get("last_sync"),
                    "last_sync_at": row.last_sync_at.isoformat() if row.last_sync_at else None, "sync_requested": bool(settings.get("sync_requested"))})
    return out


def save_config(db, user, body: dict) -> None:
    row = connection_row(db)
    url = normalize_url(str(body.get("url") or ""))
    username = str(body.get("username") or "").strip()[:80]
    password = body.get("password") if isinstance(body.get("password"), str) else ""
    if not username:
        raise ValueError("Indica l'utente RouterOS di sola lettura.")
    if row is None and not password:
        raise ValueError("Indica la password dell'utente RouterOS.")
    if row is not None and not password:
        password = _credentials(row)[1]
    try:
        interval = max(1, min(60, int(body.get("interval_minutes") or DEFAULT_INTERVAL)))
    except (TypeError, ValueError):
        raise ValueError("Intervallo non valido (1–60 minuti).") from None
    secret = encrypt_text(json.dumps({"username": username, "password": password}))
    if row is None:
        row = ConnectorIntegration(provider=PROVIDER, name="BRAS MikroTik", base_url=url, secret_encrypted=secret, settings={})
        db.add(row)
    row.base_url, row.secret_encrypted = url, secret
    row.verify_tls, row.is_enabled = body.get("verify_tls") is True, body.get("enabled") is not False
    row.settings = {**(row.settings or {}), "interval_minutes": interval}
    core.add_event(db, "BRAS_CONFIG_SAVED", actor=user, source="portal",
                   details={"url": url, "username": username, "verify_tls": row.verify_tls, "enabled": row.is_enabled, "interval_minutes": interval,
                            "password_changed": bool(body.get("password"))})


def test_connection(row) -> tuple[bool, str]:
    started = time.monotonic()
    try:
        sessions = fetch_sessions(row)
    except BrasError as exc:
        row.last_test_status, row.last_error, row.last_tested_at = "failed", str(exc)[:500], utcnow()
        return False, str(exc)
    row.last_test_status, row.last_error, row.last_tested_at = "success", None, utcnow()
    return True, f"BRAS raggiungibile: {len(sessions)} sessioni PPP attive lette in {time.monotonic() - started:.1f} s."


# --- Routes ----------------------------------------------------------------------------------------

def _write(request: Request, action):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        result = action(db, user)
        db.commit()
        return result


@router.get("/admin/integrations/bras")
def admin_bras(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return _payload(db)


@router.post("/admin/integrations/bras", dependencies=[Depends(require_csrf)])
def admin_bras_save(request: Request, body: dict = Body(default={})):
    def save(db, user):
        try:
            save_config(db, user, body)
        except (ValueError, BrasError) as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        db.flush()
        return _payload(db)

    return _write(request, save)


@router.post("/admin/integrations/bras/test", dependencies=[Depends(require_csrf)])
def admin_bras_test(request: Request):
    def run(db, user):
        row = connection_row(db)
        if row is None:
            raise HTTPException(422, {"code": "not_configured", "message": "Configura prima il BRAS."})
        ok, message = test_connection(row)
        return {"ok": ok, "message": message}

    return _write(request, run)


@router.post("/admin/integrations/bras/sync", dependencies=[Depends(require_csrf)])
def admin_bras_sync(request: Request):
    """Read the BRAS now (one ``/ppp/active`` print)."""
    def run(db, user):
        row = connection_row(db)
        if row is None or not row.is_enabled:
            raise HTTPException(422, {"code": "not_configured", "message": "Configura e attiva prima il BRAS."})
        core.add_event(db, "BRAS_SYNC_REQUESTED", actor=user, source="portal")
        result = sync(db)
        if result.get("status") == "failed":
            db.commit()
            raise HTTPException(422, {"code": "bras", "message": result["error"]})
        return {**_payload(db), "result": {k: v for k, v in result.items() if k != "matching"}, "matching": result.get("matching")}

    return _write(request, run)


@router.get("/customers/{customer_id}/pppoe")
def customer_pppoe(request: Request, customer_id: uuid.UUID):
    with SessionLocal() as db:
        ui_user(request, db, "customers.read")
        customer = db.get(Customer, customer_id)
        if customer is None:
            raise HTTPException(404, "Cliente non trovato.")
        row = connection_row(db)
        return {"bras": bool(row and row.is_enabled), "last_sync_at": row.last_sync_at.isoformat() if row and row.last_sync_at else None,
                "accounts": customer_connections(db, customer)}


def install_mikrotik_bras(app) -> None:
    app.include_router(router)
