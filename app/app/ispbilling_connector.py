"""ISPBilling connector (ISPB-01): read the operator's billing/CRM registry to import customers and sites into NSM.

- **API**: ``GET {base}/api/modules/<group>/<resource>``, header ``Authorization: Bearer {api_id}:{api_key}``
  (a key created in ISPBilling with read permissions and the NSM server among its allowed IPs). Lists answer
  ``{"status", "data": {"data": [...], "total", "pages", "next", ...}}`` 20 rows at a time (``?page=N``).
- **Customers** (``crm/customers``): kept in ``ispbilling_customers`` with name, internal code, kind, status, group
  and registered address only — never fiscal data, contacts, payment data or the ``password`` field.
- **Sites**: the installation address of a service is only in the detail of the service instance
  (``subscription-services/service-instances/{id}`` → ``address``). The list of instances is read at every sync;
  details are read only for instances whose address is not known yet or that changed, ``DETAIL_BUDGET`` per worker
  cycle, so the first full read is spread over a few cycles.
- **RADIUS accounts** (``ispradius2/accounts``): read at every full sync and matched to NSM devices
  (``ispbilling_radius``), shown on the device page.
- **Schedule**: the worker syncs every ``SYNC_INTERVAL``, or at its next cycle when an administrator asks for it,
  and keeps reading pending details at every cycle until none is left.

Nothing is written to ISPBilling. Customers and sites are created in NSM from the import page, or after each
successful read when the automatic import is switched on (``ispbilling_import.auto_import``).
"""
from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from app import ispbilling_radius
from app import main as core
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.ispbilling_models import IspbillingAddress, IspbillingCustomer, IspbillingRadiusAccount, IspbillingServiceInstance
from app.models import utcnow
from app.secret_vault import decrypt_text, encrypt_text
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
PROVIDER = "ispbilling"
DEFAULT_BASE_URL = "https://ispbilling.it"
API = "/api/modules"
TIMEOUT = 30.0
MAX_PAGES = 500
SYNC_INTERVAL = timedelta(hours=6)
RETRY_AFTER = timedelta(minutes=30)
DETAIL_BUDGET = 150
GENERIC_LABELS = {"", "indirizzo principale", "indirizzo installazione", "indirizzo", "sede"}


class IspbillingError(RuntimeError):
    pass


def normalize_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/") or DEFAULT_BASE_URL
    parsed = urlsplit(raw)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Inserisci l'indirizzo di ISPBilling con https:// (di norma https://ispbilling.it).")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("L'indirizzo non deve contenere credenziali, query o fragment.")
    path = (parsed.path or "").rstrip("/")
    for suffix in (API, "/api/docs", "/api"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    return f"https://{parsed.netloc}{path}"


# --- API client -----------------------------------------------------------------------------------

class Client:
    def __init__(self, base_url: str, api_id: str, api_key: str, transport=None):
        self.base = base_url.rstrip("/") + API
        self.http = httpx.Client(timeout=TIMEOUT, follow_redirects=False, transport=transport,
                                 headers={"Accept": "application/json", "Authorization": f"Bearer {api_id}:{api_key}"})

    def close(self):
        self.http.close()

    def get(self, path: str, params=None, what: str = "la richiesta"):
        try:
            response = self.http.get(f"{self.base}{path}", params=params)
        except httpx.HTTPError as exc:
            raise IspbillingError(f"ISPBilling non raggiungibile: {type(exc).__name__}.") from exc
        if response.status_code in (401, 403):
            raise IspbillingError(f"ISPBilling ha negato {what}: verifica la chiave API, i suoi permessi e gli IP autorizzati.")
        if response.status_code != 200:
            raise IspbillingError(f"ISPBilling ha risposto HTTP {response.status_code} a {what}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise IspbillingError("ISPBilling ha restituito una risposta non valida.") from exc
        if not isinstance(payload, dict):
            raise IspbillingError("ISPBilling ha restituito una risposta non valida.")
        return payload.get("data")

    def paged(self, path: str, what: str) -> list[dict]:
        rows, page = [], 1
        for _ in range(MAX_PAGES):
            data = self.get(path, {"page": page}, what)
            if isinstance(data, list):  # some resources are not paginated
                return [r for r in data if isinstance(r, dict)]
            items = [r for r in (data or {}).get("data") or [] if isinstance(r, dict)]
            rows.extend(items)
            following = (data or {}).get("next")
            if not items or not following or int(following) <= page:
                break
            page = int(following)
        return rows


def connection_row(db):
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == PROVIDER))


def _secret(row) -> dict:
    try:
        return json.loads(decrypt_text(row.secret_encrypted))
    except (ValueError, TypeError) as exc:
        raise IspbillingError("Chiave ISPBilling non leggibile: salvala di nuovo.") from exc


def api_id_of(row) -> str:
    """The API id (not secret, shown in the form); empty when unreadable."""
    try:
        return _secret(row).get("api_id", "") if row else ""
    except IspbillingError:
        return ""


def client_for(row, transport=None) -> Client:
    secret = _secret(row)
    return Client(row.base_url, secret.get("api_id", ""), secret.get("api_key", ""), transport)


def save_config(db, user, *, base_url: str, api_id: str, api_key: str, enabled: bool):
    """Store the connection (key encrypted, kept when left empty), audited. ValueError with the reason. Caller commits."""
    row = connection_row(db)
    url = normalize_base_url(base_url)
    api_id, key = (api_id or "").strip(), (api_key or "").strip()
    if ":" in key and not api_id:  # pasted as "id:key"
        api_id, key = key.split(":", 1)
    if not key and row:
        key = _secret(row).get("api_key", "")
    if not api_id or not key:
        raise ValueError("Indica API id e API key della chiave creata in ISPBilling.")
    if row is None:
        row = ConnectorIntegration(provider=PROVIDER, name="ISPBilling", base_url=url, secret_encrypted="", settings={})
        db.add(row)
    row.base_url, row.is_enabled, row.verify_tls = url, enabled, True
    row.secret_encrypted = encrypt_text(json.dumps({"api_id": api_id, "api_key": key}))
    core.add_event(db, "ISPBILLING_CONFIGURED", actor=user, details={"base_url": url, "enabled": enabled}, source="portal")
    return row


def test_connection(row, transport=None) -> tuple[bool, str]:
    """Read the first page of customers; the outcome is stored on the row. Caller commits."""
    try:
        client = client_for(row, transport)
        try:
            data = client.get("/crm/customers", {"page": 1}, "l'elenco clienti")
        finally:
            client.close()
        total = (data or {}).get("total") if isinstance(data, dict) else None
        row.last_tested_at, row.last_test_status, row.last_error = utcnow(), "success", None
        return True, f"Connessione riuscita: {total if total is not None else 'elenco'} clienti leggibili."
    except IspbillingError as exc:
        row.last_tested_at, row.last_test_status, row.last_error = utcnow(), "failed", str(exc)[:500]
        return False, str(exc)


# --- Normalization --------------------------------------------------------------------------------

def _text(value, limit: int) -> str | None:
    value = " ".join(str(value).split()) if value is not None else ""
    return value[:limit] or None


def customer_name(row: dict) -> str:
    """Company name for companies and associations, else first and last name, else whatever ISPBilling shows."""
    person = " ".join(part for part in (_text(row.get("first_name"), 100), _text(row.get("last_name"), 100)) if part)
    business = _text(row.get("business_name"), 200)
    if str(row.get("type") or "").lower() != "private" and business:
        return business
    return (person or business or _text(row.get("customer_name"), 200) or f"Cliente ISPBilling {row.get('customer_id')}")[:200]


def format_address(row: dict) -> str | None:
    """One line: street, line 2, postcode city (province), country when not Italy."""
    street = ", ".join(part for part in (_text(row.get("address_line1"), 150), _text(row.get("address_line2"), 100)) if part)
    place = " ".join(part for part in (_text(row.get("postal_code"), 12), _text(row.get("city"), 100)) if part)
    province = _text(row.get("state_code"), 10)
    if province:
        place = f"{place} ({province})" if place else province
    country = _text(row.get("country_code"), 4)
    line = ", ".join(part for part in (street, place, country if country and country.upper() != "IT" else None) if part)
    return line[:300] or None


def site_name(label: str | None, address: str | None) -> str:
    """The ISPBilling label of the address when it names the place (e.g. "Sede centrale"), else the address itself."""
    if label and label.strip().lower() not in GENERIC_LABELS:
        return label[:200]
    return (address or "Sede ISPBilling")[:200]


def normalized(value: str | None) -> str:
    return " ".join(str(value or "").lower().replace(",", " ").replace(".", " ").split())


# --- Sync -----------------------------------------------------------------------------------------

def _upsert_customers(db, rows: list[dict], now) -> int:
    existing = {c.external_id: c for c in db.scalars(select(IspbillingCustomer))}
    for row in rows:
        external = _text(row.get("customer_id"), 40)
        if not external:
            continue
        item = existing.get(external)
        if item is None:
            item = existing[external] = IspbillingCustomer(external_id=external, name="")
            db.add(item)
        item.internal_code = _text(row.get("internal_code"), 80)
        item.name = customer_name(row)
        item.kind = _text(row.get("type"), 30)
        item.status = _text(row.get("status"), 40)
        item.group_name = _text(row.get("group_name"), 120)
        item.address = format_address(row)
        item.seen_at = now
    return len(rows)


def _upsert_instances(db, rows: list[dict], now) -> int:
    existing = {i.external_id: i for i in db.scalars(select(IspbillingServiceInstance))}
    for row in rows:
        external = _text(row.get("service_instance_id"), 40)
        customer = _text(row.get("customer_id"), 40)
        if not external or not customer:
            continue
        item = existing.get(external)
        updated = _text(row.get("updated_at"), 40)
        address = _text(row.get("address_id"), 40)
        if item is None:
            item = existing[external] = IspbillingServiceInstance(external_id=external, customer_external_id=customer)
            db.add(item)
        elif item.remote_updated_at != updated or item.address_external_id != address:
            item.detail_read_at = None  # changed: its address may have changed too
        item.customer_external_id, item.address_external_id, item.remote_updated_at = customer, address, updated
        item.name, item.status, item.seen_at = _text(row.get("name"), 200), _text(row.get("status"), 40), now
    return len(rows)


def read_pending_details(db, client, now, budget: int = DETAIL_BUDGET) -> dict:
    """Read the address of new or changed instances; an address read in this same pass for another instance is reused."""
    known = {a.external_id: a for a in db.scalars(select(IspbillingAddress))}
    fresh: set[str] = set()
    read = reused = 0
    pending = list(db.scalars(select(IspbillingServiceInstance).where(IspbillingServiceInstance.detail_read_at.is_(None))
                              .order_by(IspbillingServiceInstance.customer_external_id)))
    for instance in pending:
        if instance.address_external_id and instance.address_external_id in fresh:
            instance.detail_read_at = now  # same address just read through another service
            reused += 1
            continue
        if read >= budget:
            break
        data = client.get(f"/subscription-services/service-instances/{instance.external_id}", None, "il dettaglio di un servizio")
        read += 1
        address = (data or {}).get("address") if isinstance(data, dict) else None
        instance.detail_read_at = now
        if not isinstance(address, dict):
            continue
        external = _text(address.get("address_id"), 40) or instance.address_external_id
        text = format_address(address)
        if not external or not text:
            continue
        item = known.get(external)
        if item is None:
            item = known[external] = IspbillingAddress(external_id=external, customer_external_id=instance.customer_external_id, address=text)
            db.add(item)
        item.customer_external_id, item.address = instance.customer_external_id, text
        item.label, item.seen_at = _text(address.get("description"), 200), now
        fresh.add(external)
    db.flush()
    remaining = int(db.scalar(select(func.count()).select_from(IspbillingServiceInstance)
                              .where(IspbillingServiceInstance.detail_read_at.is_(None))) or 0)
    return {"details_read": read, "details_reused": reused, "details_pending": remaining}


def sync(db, row=None, now=None, transport=None, full: bool = True) -> dict:
    """Refresh the mirror: customers and instances (``full``), then pending details. Caller-independent: commits."""
    now = now or utcnow()
    row = row or connection_row(db)
    if row is None or not row.is_enabled:
        return {"status": "disabled"}
    stats: dict = {"status": "success"}
    try:
        client = client_for(row, transport)
        try:
            if full:
                stats["customers"] = _upsert_customers(db, client.paged("/crm/customers", "l'elenco clienti"), now)
                stats["service_instances"] = _upsert_instances(db, client.paged("/subscription-services/service-instances", "l'elenco servizi"), now)
                db.flush()
                # RADIUS accounts are optional (the key may lack the Radius permission): their error does not stop the read.
                try:
                    stats["radius_accounts"] = ispbilling_radius.upsert_accounts(db, client.paged("/ispradius2/accounts", "gli account RADIUS"), now)
                except IspbillingError as exc:
                    stats["radius_error"] = str(exc)[:300]
                db.flush()
                stats["radius_matched"] = {k: v for k, v in ispbilling_radius.match_devices(db).items() if k != "accounts"}
                db.flush()
            stats.update(read_pending_details(db, client, now))
        finally:
            client.close()
    except IspbillingError as exc:
        db.rollback()
        row = connection_row(db)
        stats = {"status": "failed", "error": str(exc)[:300]}
    settings = dict(row.settings or {})
    if full or stats["status"] == "failed":
        settings["last_sync"] = {**stats, "at": now.isoformat()}
        settings.pop("sync_requested", None)
        # A failed read is retried after RETRY_AFTER, not after the whole interval.
        row.last_sync_at = now if stats["status"] == "success" else now - SYNC_INTERVAL + RETRY_AFTER
    else:
        settings["last_sync"] = {**(settings.get("last_sync") or {}), **stats}
    row.settings = settings
    db.commit()
    return stats


def scheduled_sync(now=None, transport=None) -> dict:
    """Worker task: full sync when due or requested, else keep reading pending addresses; then the automatic import."""
    from app.ispbilling_import import auto_import  # it imports this module

    now = now or utcnow()
    with SessionLocal() as db:
        row = connection_row(db)
        if row is None or not row.is_enabled:
            return {"status": "disabled"}
        requested = bool((row.settings or {}).get("sync_requested"))
        if requested or row.last_sync_at is None or now - row.last_sync_at >= SYNC_INTERVAL:
            stats = sync(db, row, now, transport, full=True)
        else:
            pending = db.scalar(select(func.count()).select_from(IspbillingServiceInstance).where(IspbillingServiceInstance.detail_read_at.is_(None)))
            if not pending:
                return {"status": "not_due"}
            stats = sync(db, row, now, transport, full=False)
        if stats.get("status") == "success":
            imported = auto_import(db, connection_row(db), now)
            if imported is not None:
                stats["auto_import"] = imported
        return stats


def save_auto_import(db, user, enabled: bool) -> None:
    """Switch the automatic import of new customers and sites on or off, audited. Caller commits."""
    row = connection_row(db)
    if row is None:
        raise ValueError("Configura prima ISPBilling.")
    row.settings = {**(row.settings or {}), "auto_import": bool(enabled)}
    core.add_event(db, "ISPBILLING_AUTO_IMPORT_CHANGED", actor=user, details={"enabled": bool(enabled)}, source="portal")


def summary(db) -> dict:
    row = connection_row(db)
    customers = int(db.scalar(select(func.count()).select_from(IspbillingCustomer)) or 0)
    linked = int(db.scalar(select(func.count()).select_from(IspbillingCustomer).where(IspbillingCustomer.nsm_customer_id.is_not(None))) or 0)
    pending = int(db.scalar(select(func.count()).select_from(IspbillingServiceInstance).where(IspbillingServiceInstance.detail_read_at.is_(None))) or 0)
    addresses = int(db.scalar(select(func.count()).select_from(IspbillingAddress)) or 0)
    accounts = int(db.scalar(select(func.count()).select_from(IspbillingRadiusAccount)) or 0)
    matched = int(db.scalar(select(func.count()).select_from(IspbillingRadiusAccount).where(IspbillingRadiusAccount.nsm_device_id.is_not(None))) or 0)
    return {"connection": row, "customers": customers, "linked": linked, "addresses": addresses, "details_pending": pending,
            "radius_accounts": accounts, "radius_matched": matched}


# --- Administration API ---------------------------------------------------------------------------

def _payload(db) -> dict:
    info = summary(db)
    row = info["connection"]
    settings = (row.settings or {}) if row else {}
    return jsonable_encoder({
        "configured": row is not None, "enabled": bool(row and row.is_enabled), "base_url": row.base_url if row else DEFAULT_BASE_URL,
        "api_id": api_id_of(row), "last_test_status": row.last_test_status if row else None, "last_error": row.last_error if row else None,
        "last_tested_at": row.last_tested_at if row else None, "last_sync_at": row.last_sync_at if row else None,
        "last_sync": settings.get("last_sync") or {}, "sync_requested": bool(settings.get("sync_requested")),
        "auto_import": bool(settings.get("auto_import")), "last_auto_import": settings.get("last_auto_import"),
        "customers": info["customers"], "linked": info["linked"], "addresses": info["addresses"], "details_pending": info["details_pending"],
        "radius_accounts": info["radius_accounts"], "radius_matched": info["radius_matched"],
    })


def _write(request: Request, action):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        result = action(db, user)
        db.commit()
        return result


def _body_text(body: dict, key: str) -> str:
    value = body.get(key)
    return value if isinstance(value, str) else ""


@router.get("/admin/integrations/ispbilling")
def admin_ispbilling(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return _payload(db)


@router.post("/admin/integrations/ispbilling", dependencies=[Depends(require_csrf)])
def admin_ispbilling_save(request: Request, body: dict = Body(default={})):
    def save(db, user):
        try:
            save_config(db, user, base_url=_body_text(body, "base_url"), api_id=_body_text(body, "api_id"),
                        api_key=_body_text(body, "api_key"), enabled=body.get("enabled") is not False)
        except (ValueError, IspbillingError) as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        db.flush()
        return _payload(db)

    return _write(request, save)


@router.post("/admin/integrations/ispbilling/test", dependencies=[Depends(require_csrf)])
def admin_ispbilling_test(request: Request):
    def run(db, user):
        row = connection_row(db)
        if row is None:
            raise HTTPException(422, {"code": "not_configured", "message": "Configura prima ISPBilling."})
        ok, message = test_connection(row)
        return {"ok": ok, "message": message}

    return _write(request, run)


@router.post("/admin/integrations/ispbilling/sync", dependencies=[Depends(require_csrf)])
def admin_ispbilling_sync(request: Request):
    """Ask the worker for a full read at its next cycle (a full read takes longer than a request)."""
    def run(db, user):
        row = connection_row(db)
        if row is None or not row.is_enabled:
            raise HTTPException(422, {"code": "not_configured", "message": "Configura e attiva prima ISPBilling."})
        row.settings = {**(row.settings or {}), "sync_requested": True}
        core.add_event(db, "ISPBILLING_SYNC_REQUESTED", actor=user, source="portal")
        db.flush()
        return _payload(db)

    return _write(request, run)


@router.post("/admin/integrations/ispbilling/auto-import", dependencies=[Depends(require_csrf)])
def admin_ispbilling_auto_import(request: Request, body: dict = Body(default={})):
    def run(db, user):
        try:
            save_auto_import(db, user, body.get("enabled") is True)
        except ValueError as error:
            raise HTTPException(422, {"code": "not_configured", "message": str(error)}) from None
        db.flush()
        return _payload(db)

    return _write(request, run)


def install_ispbilling_connector(app) -> None:
    app.include_router(router)
