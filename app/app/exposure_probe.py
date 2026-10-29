"""External probe connector (SCAN-02): check exposed ports from outside the operator network.

The NSM server usually sits inside the operator network: probing a customer's public address from there does not cross
the operator's border filters, so "open" may only mean "open from inside". The external probe (``probe/``, a small
container on any host outside the network) runs the same checks from Internet.

- Settings (*Amministrazione → Integrazioni → Sonda esterna*, ``users.manage``): address, shared secret (encrypted,
  never returned), name, TLS check, on/off; *Test* reads ``/v1/health`` and sends an empty signed scan.
- When the probe is on, the external exposure check of a public address goes through it; the result records the
  vantage point (``vantage``: ``probe`` with its name, or ``nsm_server``). If the probe does not answer, NSM checks
  from its own server and says why (``vantage_error``).
- Requests are signed (HMAC-SHA256 of ``"<timestamp>.<body>"``) and carry only public addresses of the inventory and
  the planned ports.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import time
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import utcnow
from app.secret_vault import decrypt_text, encrypt_text
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
PROVIDER = "exposure_probe"
TIMEOUT = 30.0
MIN_SECRET = 32


class ProbeError(RuntimeError):
    pass


def connection_row(db):
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == PROVIDER))


def normalize_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Indica l'indirizzo della sonda con http:// o https:// (es. https://probe.example.net:8788).")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("L'indirizzo non deve contenere credenziali, query o fragment.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        if address.is_loopback or address.is_unspecified or address.is_link_local:
            raise ValueError("Indirizzo non consentito: la sonda deve stare fuori dalla rete.")
    except ValueError as exc:
        if "non consentito" in str(exc):
            raise
    return f"{parsed.scheme}://{parsed.netloc}{(parsed.path or '').rstrip('/')}"


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def _secret(row) -> str:
    try:
        return json.loads(decrypt_text(row.secret_encrypted)).get("secret", "")
    except (ValueError, TypeError) as exc:
        raise ProbeError("Segreto della sonda non leggibile: salvalo di nuovo.") from exc


def name_of(row) -> str:
    return str((row.settings or {}).get("name") or "sonda esterna") if row else ""


def active(db) -> ConnectorIntegration | None:
    row = connection_row(db)
    return row if row is not None and row.is_enabled else None


def _client(row, transport=None) -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, verify=bool(row.verify_tls), follow_redirects=False, transport=transport)


def _post_scan(row, payload: dict, transport=None) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode()
    stamp = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-NSM-Timestamp": stamp, "X-NSM-Signature": sign(_secret(row), stamp, body)}
    try:
        with _client(row, transport) as client:
            response = client.post(f"{row.base_url}/v1/scan", content=body, headers=headers)
    except httpx.HTTPError as exc:
        raise ProbeError(f"Sonda non raggiungibile: {type(exc).__name__}.") from exc
    if response.status_code == 401:
        raise ProbeError("La sonda ha rifiutato la firma: il segreto o l'orologio del server non coincidono.")
    if response.status_code == 403:
        raise ProbeError("La sonda non accetta richieste da questo indirizzo (PROBE_ALLOWED_SOURCES).")
    if response.status_code == 429:
        raise ProbeError("La sonda ha ricevuto troppe richieste: riprova tra un minuto.")
    if response.status_code != 200:
        raise ProbeError(f"La sonda ha risposto HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError as exc:
        raise ProbeError("Risposta della sonda non valida.") from exc


def scan(row, ip: str, checks: list[tuple[str, int, str]], timeout: float = 2.0, transport=None) -> dict[str, tuple[str, str]]:
    """{key: (state, reason)} from the probe for ``checks`` = [(key, port, proto)]."""
    payload = {"targets": [{"ip": ip, "checks": [{"key": k, "port": p, "proto": proto} for k, p, proto in checks]}], "timeout": timeout}
    answer = _post_scan(row, payload, transport)
    rows = next((r.get("findings") or [] for r in answer.get("results") or [] if r.get("ip") == ip), [])
    return {str(r.get("key")): (str(r.get("state") or "filtered"), str(r.get("reason") or "")) for r in rows if isinstance(r, dict)}


def test_connection(row, transport=None) -> tuple[bool, str]:
    row.last_tested_at = utcnow()
    try:
        with _client(row, transport) as client:
            health = client.get(f"{row.base_url}/v1/health")
        if health.status_code != 200:
            raise ProbeError(f"La sonda ha risposto HTTP {health.status_code} a /v1/health.")
        info = health.json()
        _post_scan(row, {"targets": [], "timeout": 1}, transport)
    except (ProbeError, httpx.HTTPError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ProbeError) else f"Sonda non raggiungibile: {type(exc).__name__}."
        row.last_test_status, row.last_error = "failed", message
        return False, message
    row.last_test_status, row.last_error = "success", None
    return True, f"Sonda «{info.get('name') or name_of(row)}» raggiungibile (versione {info.get('version') or '?'}): firma accettata."


def save_config(db, user, *, base_url: str, secret: str, name: str, verify_tls: bool, enabled: bool):
    row = connection_row(db)
    url = normalize_url(base_url)
    value = (secret or "").strip()
    if not value and row:
        value = _secret(row)
    if len(value) < MIN_SECRET:
        raise ValueError(f"Il segreto condiviso deve avere almeno {MIN_SECRET} caratteri (lo stesso di PROBE_SECRET sulla sonda).")
    if row is None:
        row = ConnectorIntegration(provider=PROVIDER, name="Sonda esterna", base_url=url, secret_encrypted="", settings={})
        db.add(row)
    row.base_url, row.verify_tls, row.is_enabled = url, verify_tls, enabled
    row.settings = {**(row.settings or {}), "name": (name or "").strip()[:60] or "sonda esterna"}
    row.secret_encrypted = encrypt_text(json.dumps({"secret": value}))
    core.add_event(db, "EXPOSURE_PROBE_CONFIGURED", actor=user, details={"base_url": url, "enabled": enabled, "name": row.settings["name"]}, source="portal")
    return row


def _payload(db) -> dict:
    row = connection_row(db)
    return {"configured": row is not None, "enabled": bool(row and row.is_enabled), "base_url": row.base_url if row else "",
            "name": name_of(row), "verify_tls": bool(row.verify_tls) if row else True,
            "last_test_status": row.last_test_status if row else None, "last_error": row.last_error if row else None,
            "last_tested_at": row.last_tested_at.isoformat() if row and row.last_tested_at else None}


@router.get("/admin/integrations/probe")
def admin_probe(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return _payload(db)


@router.post("/admin/integrations/probe", dependencies=[Depends(require_csrf)])
def admin_probe_save(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        try:
            save_config(db, user, base_url=str(body.get("base_url") or ""), secret=str(body.get("secret") or ""), name=str(body.get("name") or ""),
                        verify_tls=body.get("verify_tls") is not False, enabled=body.get("enabled") is True)
        except (ValueError, ProbeError) as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        db.commit()
        return _payload(db)


@router.post("/admin/integrations/probe/test", dependencies=[Depends(require_csrf)])
def admin_probe_test(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        row = connection_row(db)
        if row is None:
            raise HTTPException(422, {"code": "not_configured", "message": "Configura prima la sonda esterna."})
        ok, message = test_connection(row)
        db.commit()
        return {"ok": ok, "message": message}


def install_exposure_probe(app) -> None:
    app.include_router(router)
