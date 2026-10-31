"""UISP endpoints → NSM customers, and automatic onboarding of their devices (operator request 2026-10-23).

UISP keeps customer CPEs on *endpoint* sites, almost always linked to a client of the UISP CRM (``ucrm.client``). The
NSM customers come from ISPBilling, so the link is made by name:

- an endpoint is **matched** when the name of its CRM client, or its own name, is the name of exactly one NSM customer
  (case, accents, punctuation and the order of the words ignored: "Rossi Mario" = "Mario Rossi"); checked on the
  operator's UISP: 560 of 738 endpoints, no ambiguous case;
- the others are listed *da verificare*: the operator links them to a customer with one click (or ignores them); the
  choice is kept in the UISP connector settings (``settings["site_customers"]``, endpoint id → customer or ignored)
  and always wins over the name;
- every ``RUN_INTERVAL`` (and after *Abbina*), devices of matched or linked endpoints that are new to NSM are created
  under that customer, at its site when it has exactly one, and associated with UISP like the manual onboarding
  (``uisp_onboarding``); a device already in NSM with the same MAC is linked, never duplicated. Infrastructure
  *sites* (towers, APs, backhaul) are never imported automatically (operator decision): they stay in the manual
  onboarding. Audited ``UISP_AUTO_IMPORTED`` (per run) and ``UISP_SITE_LINKED``/``UISP_SITE_IGNORED``.
- switch: ``settings["auto_customers"]`` (on by default), *Integrazioni → UISP → Clienti UISP*.
"""
from __future__ import annotations

import re
import time
import unicodedata
import uuid
from collections import defaultdict
from datetime import datetime

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import main as core
from app import uisp_connector as uisp
from app import uisp_onboarding as onboarding
from app.db import SessionLocal
from app.models import Customer, Device, utcnow
from app.secret_vault import decrypt_text
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
RUN_INTERVAL_SECONDS = 1800
SITES_TIMEOUT_SECONDS = 90.0
MAX_PER_RUN = 300
ENDPOINTS_CACHE_SECONDS = 300
TRANSPORT = None  # tests swap in an httpx.MockTransport
_last_run = 0.0
_endpoints_cache = None  # (read at, connection, endpoints) of the last read for the page


def norm(value) -> str:
    text = unicodedata.normalize("NFD", str(value or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def name_index(customers) -> dict[str, set]:
    """Normalized name, and the name with the first or the last word moved, → customer ids."""
    index: dict[str, set] = defaultdict(set)
    for customer in customers:
        words = norm(customer.name).split()
        if not words:
            continue
        index[" ".join(words)].add(customer.id)
        if len(words) >= 2:
            index[" ".join(words[1:] + words[:1])].add(customer.id)
            index[" ".join(words[-1:] + words[:-1])].add(customer.id)
    return index


def fetch_endpoints(connection) -> list[dict]:
    """UISP endpoint sites: id, name, CRM client (one call, larger timeout than the device list)."""
    try:
        token = decrypt_text(connection.secret_encrypted)
    except ValueError as exc:
        raise uisp.UispConnectorError("Impossibile decifrare il token UISP configurato.") from exc
    url = f"{connection.base_url.rstrip('/')}{uisp.UISP_API_PATH.rsplit('/', 1)[0]}/sites"
    try:
        with httpx.Client(timeout=SITES_TIMEOUT_SECONDS, verify=bool(connection.verify_tls), follow_redirects=False, transport=TRANSPORT,
                          headers={"Accept": "application/json", "x-auth-token": token}) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise uisp.UispConnectorError(f"UISP non raggiungibile leggendo i siti ({type(exc).__name__}).") from exc
    if response.status_code != 200:
        raise uisp.UispConnectorError(f"UISP ha risposto HTTP {response.status_code} leggendo i siti.")
    out = []
    for site in response.json() or []:
        ident = site.get("identification") or {}
        if ident.get("type") != "endpoint" or not site.get("id"):
            continue
        client = (site.get("ucrm") or {}).get("client") or {}
        out.append({"id": str(site["id"]), "name": str(ident.get("name") or "")[:200], "client": str(client.get("name") or "")[:200] or None,
                    "parent": str((ident.get("parent") or {}).get("name") or "")[:200] or None})
    return out


def cached_endpoints(connection) -> list[dict]:
    """The site list takes UISP about 8 seconds: the page and its buttons read it once every few minutes; the worker always reads it."""
    global _endpoints_cache
    key = (connection.base_url, connection.secret_encrypted)
    if _endpoints_cache and _endpoints_cache[1] == key and time.monotonic() - _endpoints_cache[0] < ENDPOINTS_CACHE_SECONDS:
        return _endpoints_cache[2]
    endpoints = fetch_endpoints(connection)
    _endpoints_cache = (time.monotonic(), key, endpoints)
    return endpoints


def decisions(connection) -> dict:
    return dict(((connection.settings or {}).get("site_customers") or {})) if connection else {}


def auto_enabled(connection) -> bool:
    return bool(((connection.settings or {}) if connection else {}).get("auto_customers", True))


def resolve(endpoints: list[dict], customers, choices: dict) -> list[dict]:
    """Each endpoint with its NSM customer: chosen by the operator, else the unique name match, else none."""
    index = name_index(customers)
    by_id = {c.id: c for c in customers}
    rows = []
    for endpoint in endpoints:
        choice = choices.get(endpoint["id"]) or {}
        state, customer = "unmatched", None
        if choice.get("ignored"):
            state = "ignored"
        elif choice.get("customer_id"):
            try:
                customer = by_id.get(uuid.UUID(choice["customer_id"]))
            except ValueError:
                customer = None
            state = "linked" if customer else "unmatched"
        else:
            found = set()
            for name in (endpoint.get("client"), endpoint.get("name")):
                hits = index.get(norm(name), set()) if name else set()
                if len(hits) == 1:
                    found |= hits
            if len(found) == 1:
                state, customer = "matched", by_id[next(iter(found))]
        rows.append({**endpoint, "state": state, "customer": customer})
    return rows


def _site_for(customer):
    sites = list(customer.sites or [])
    return sites[0] if len(sites) == 1 else None


def import_devices(db, connection, rows: list[dict], actor=None, now: datetime | None = None) -> dict:
    """Create or link the new UISP devices of matched/linked endpoints under their customer. Caller commits."""
    targets = {row["id"]: row["customer"] for row in rows if row["state"] in ("matched", "linked") and row["customer"] is not None}
    stats = {"created": 0, "linked": 0, "customers": 0}
    if not targets:
        return stats
    candidates = onboarding.classify(db, uisp._fetch_candidates(connection))
    touched = set()
    for row in candidates:
        if stats["created"] + stats["linked"] >= MAX_PER_RUN:
            break
        site = row.get("site") or {}
        customer = targets.get(str(site.get("id") or ""))
        if customer is None or site.get("type") != "endpoint" or row["state"] not in ("new", "linkable"):
            continue
        if row["state"] == "new":
            nsm_site = _site_for(customer)
            device = Device(customer_id=customer.id, site_id=nsm_site.id if nsm_site else None, vendor="ubiquiti", device_type=row["device_type"],
                            name=row.get("device_identity") or f"UISP {row['primary_mac']}", primary_mac=row["primary_mac"],
                            management_source="uisp", status="pending_link")
            db.add(device)
            db.flush()
            core.add_event(db, "DEVICE_ADDED", actor=actor, customer_id=customer.id, device_id=device.id, source="portal" if actor else "worker",
                           details={"vendor": "ubiquiti", "management_source": "uisp", "source": "uisp_auto_customer", "uisp_site": site.get("name")})
            stats["created"] += 1
        else:
            device = row["device"]
            stats["linked"] += 1
        uisp.apply_candidate(db, device, row, actor, event_type="UISP_DEVICE_ASSOCIATED")
        touched.add(customer.id)
    stats["customers"] = len(touched)
    if stats["created"] or stats["linked"]:
        core.add_event(db, "UISP_AUTO_IMPORTED", actor=actor, source="portal" if actor else "worker", details=stats)
    return stats


def _context(db):
    connection = uisp._connection(db, enabled_only=True)
    customers = list(db.scalars(select(Customer).options(selectinload(Customer.sites))))
    return connection, customers


def run(now: datetime | None = None, force: bool = False) -> dict:
    """Worker task: automatic onboarding of the devices of matched or linked UISP endpoints."""
    global _last_run
    if not force and time.monotonic() - _last_run < RUN_INTERVAL_SECONDS:
        return {"status": "not_due"}
    _last_run = time.monotonic()
    with SessionLocal() as db:
        try:
            connection, customers = _context(db)
        except HTTPException:
            return {"status": "disabled"}
        if connection is None or not auto_enabled(connection):
            return {"status": "disabled"}
        try:
            rows = resolve(fetch_endpoints(connection), customers, decisions(connection))
            stats = import_devices(db, connection, rows, now=now)
        except uisp.UispConnectorError as exc:
            return {"status": "failed", "error": str(exc)[:200]}
        db.commit()
        return {"status": "success", **stats}


# --- Routes ------------------------------------------------------------------------------------

def _payload(db, connection, rows, can_write: bool) -> dict:
    device_counts = defaultdict(int)
    for site_id in db.scalars(select(Device.inventory_data["uisp"]["site"]["id"].as_string()).where(Device.vendor == "ubiquiti")):
        if site_id:
            device_counts[site_id] += 1
    counts = {state: sum(1 for r in rows if r["state"] == state) for state in ("matched", "linked", "unmatched", "ignored")}
    order = {"unmatched": 0, "linked": 1, "matched": 2, "ignored": 3}
    rows = sorted(rows, key=lambda r: (order[r["state"]], norm(r.get("client") or r["name"])))
    return {"auto": auto_enabled(connection), "counts": counts, "can_write": can_write,
            "items": [{"id": r["id"], "name": r["name"], "client": r.get("client"), "parent": r.get("parent"), "state": r["state"],
                       "customer": {"id": str(r["customer"].id), "name": r["customer"].name, "code": r["customer"].code} if r["customer"] else None,
                       "devices_in_nsm": device_counts.get(r["id"], 0)} for r in rows]}


@router.get("/integrations/uisp/site-customers")
def uisp_site_customers(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        connection, customers = _context(db)
        if connection is None:
            raise HTTPException(409, {"code": "not_configured", "message": "UISP non è configurato o è disattivato."})
        try:
            rows = resolve(cached_endpoints(connection), customers, decisions(connection))
        except uisp.UispConnectorError as exc:
            raise HTTPException(502, {"code": "uisp", "message": str(exc)}) from None
        return _payload(db, connection, rows, core.has_permission(user, "devices.write"))


@router.post("/integrations/uisp/site-customers", dependencies=[Depends(require_csrf)])
def uisp_site_customers_save(request: Request, body: dict = Body(default={})):
    """Link an endpoint to a customer (``customer_id``), ignore it (``ignore``), forget the choice (``reset``), or switch ``auto``."""
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        connection, customers = _context(db)
        if connection is None:
            raise HTTPException(409, {"code": "not_configured", "message": "UISP non è configurato o è disattivato."})
        stored = dict(connection.settings or {})
        choices = dict(stored.get("site_customers") or {})
        site_id = str(body.get("site_id") or "")[:64]
        if "auto" in body:
            stored["auto_customers"] = bool(body["auto"])
            core.add_event(db, "UISP_AUTO_CUSTOMERS_CHANGED", actor=user, source="portal", details={"enabled": bool(body["auto"])})
        elif site_id and body.get("customer_id"):
            customer = next((c for c in customers if str(c.id) == str(body["customer_id"])), None)
            if customer is None:
                raise HTTPException(422, {"code": "customer", "message": "Cliente NSM non trovato."})
            choices[site_id] = {"customer_id": str(customer.id), "by": user.username, "at": utcnow().isoformat()}
            core.add_event(db, "UISP_SITE_LINKED", actor=user, customer_id=customer.id, source="portal",
                           details={"uisp_site": site_id, "name": str(body.get("name") or "")[:200]})
        elif site_id and body.get("ignore"):
            choices[site_id] = {"ignored": True, "by": user.username, "at": utcnow().isoformat()}
            core.add_event(db, "UISP_SITE_IGNORED", actor=user, source="portal", details={"uisp_site": site_id})
        elif site_id and body.get("reset"):
            choices.pop(site_id, None)
        else:
            raise HTTPException(422, {"code": "invalid", "message": "Richiesta non valida."})
        stored["site_customers"] = choices
        connection.settings = stored
        try:
            rows = resolve(cached_endpoints(connection), customers, choices)
            stats = import_devices(db, connection, rows, actor=user) if auto_enabled(connection) and (body.get("customer_id") or body.get("auto")) else {}
        except uisp.UispConnectorError as exc:
            db.commit()
            raise HTTPException(502, {"code": "uisp", "message": f"Scelta salvata; importazione non riuscita: {exc}"}) from None
        db.commit()
        return {**_payload(db, connection, rows, True), "imported": stats}


def install_uisp_auto_customer(app) -> None:
    app.include_router(router)
