"""Real lifecycle of the inventory models, looked up on the web (LIFE-04).

For every vendor + model in the inventory NSM asks eol.network, a free database
that collects the end-of-sale / end-of-support notices vendors publish on their
own sites (MikroTik product pages, Ubiquiti's Vintage and Legacy list, Cambium's
product lifecycle page, TP-Link's EOL lists and 90+ more vendors).  Each answer
keeps the vendor page it comes from and when it was read.

What was found is a proposal: it reaches the lifecycle catalog only when an
operator applies it (one model, or every exact match at once), through
``lifecycle_catalog.save_record`` — audited, with source, link and evidence date
like a record entered by hand.  A model with no notice is reported as such (no
end of life published when it was checked), never as "supported".

Without a key the public search is used, 10 requests a minute: a run asks at
most ``KEYLESS_PER_RUN`` models and the worker continues with the rest.  With
``EOL_NETWORK_API_KEY`` (free account) the batch match is used, 100 models a
call.  ``LIFECYCLE_WEB_LOOKUP=off`` disables every lookup.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, timedelta

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func, select

from app import lifecycle_catalog as catalog
from app import main as core
from app.db import SessionLocal
from app.input_errors import InputError
from app.lifecycle_web_models import LifecycleWebLookup
from app.models import Device, LifecycleRecord, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
PROVIDER = "eol.network"
BASE_URL = "https://eol.network"
REFRESH = timedelta(days=30)        # notices change yearly at most; the worker re-checks monthly
RETRY_ERRORS = timedelta(hours=6)
KEYLESS_PER_RUN = 8                 # the keyless search allows 10 requests a minute
BATCH = 100                         # POST /api/v1/match limit
MAX_CANDIDATES = 5
TRANSPORT = None                    # tests inject an httpx.MockTransport
# NSM vendor key → eol.network vendor slug (unknown vendors are searched across all vendors).
VENDOR_SLUGS = {
    "mikrotik": "mikrotik", "ubiquiti": "ubiquiti", "tp-link": "tplink", "tplink": "tplink", "cambium": "cambium",
    "mimosa": "mimosa", "siklu": "siklu", "teltonika": "teltonika", "zyxel": "zyxel", "netgear": "netgear",
    "cisco": "cisco", "juniper": "juniper", "fortinet": "fortinet", "aruba": "aruba", "huawei": "huawei",
    "d-link": "dlink", "dlink": "dlink", "draytek": "draytek", "ruckus": "ruckus", "sophos": "sophos",
}
# Vendor statuses that mean "no longer sold" / "no longer updated" even when no date is published.
ENDED_SALE = {"discontinued", "vintage", "legacy", "end_of_sale", "end_of_life"}
ENDED_UPDATES = {"legacy", "end_of_life"}


class LifecycleWebError(InputError, ValueError):
    pass


def enabled() -> bool:
    return os.getenv("LIFECYCLE_WEB_LOOKUP", "on").strip().lower() not in {"off", "0", "false", "no"}


def _api_key() -> str:
    return os.getenv("EOL_NETWORK_API_KEY", "").strip()


def _client() -> httpx.Client:
    headers = {"Accept": "application/json", "User-Agent": f"NSM-Platform/{core.APP_VERSION} (+https://github.com/MzTechnology97/nsm-platform)"}
    if _api_key():
        headers["X-API-Key"] = _api_key()
    return httpx.Client(base_url=BASE_URL, timeout=20.0, transport=TRANSPORT, follow_redirects=False, headers=headers)


def _norm(value) -> str:
    return re.sub(r"[^A-Z0-9+]", "", str(value or "").upper())


def _day(value) -> str | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def candidate(row: dict, model: str, confidence: str | None = None) -> dict:
    """One product as eol.network describes it, reduced to what the catalog needs."""
    source = row.get("source") or {}
    vendor_slug = row.get("vendor_slug") or row.get("vendor") or ""
    product_id = str(row.get("product_id") or "")[:150]
    exact = _norm(model) in {_norm(product_id), _norm(row.get("product_name"))}
    end_of_updates = [d for d in (_day(row.get("end_of_software_support_date")), _day(row.get("last_date_of_support"))) if d]
    status = str(row.get("lifecycle_status") or (row.get("extra") or {}).get("vendor_lifecycle_status") or "unknown")[:40]
    page = f"{BASE_URL}/{vendor_slug}/{row.get('series_slug')}/{product_id}" if vendor_slug and row.get("series_slug") and product_id else None
    url = str(source.get("url") or "")
    return {
        "product_id": product_id, "name": str(row.get("product_name") or product_id)[:200], "vendor": str(vendor_slug)[:60],
        "status": status, "end_of_sale": _day(row.get("end_of_sale_date")), "end_of_updates": min(end_of_updates) if end_of_updates else None,
        "replacement": str(row.get("replacement_product") or (row.get("extra") or {}).get("replacement_product") or "")[:200] or None,
        "source_title": str(source.get("title") or "")[:120] or None, "source_url": url[:500] if url.startswith("https://") else None,
        "page_url": page, "read_at": _day(source.get("fetched_at") or row.get("collected_at")),
        "notes": str(row.get("notes") or "")[:400] or None,
        "confidence": "exact" if exact else (confidence if confidence and confidence != "exact" else "search"),
    }


def _pairs(db) -> list[tuple[str, str]]:
    rows = db.execute(select(Device.vendor, Device.model).where(Device.model.is_not(None)).distinct()).all()
    seen, pairs = set(), []
    for vendor, model in sorted(rows, key=lambda r: (str(r[0] or ""), str(r[1] or ""))):
        vendor, model = catalog.vendor_key(vendor), re.sub(r"\s+", " ", str(model or "")).strip()
        key = catalog.model_key(vendor, model)
        if key and (vendor, key) not in seen:
            seen.add((vendor, key))
            pairs.append((vendor, model))
    return pairs


def _row(db, vendor: str, model: str) -> LifecycleWebLookup:
    key = catalog.model_key(vendor, model)
    row = db.scalar(select(LifecycleWebLookup).where(LifecycleWebLookup.vendor == vendor, LifecycleWebLookup.model_key == key))
    if row is None:
        row = LifecycleWebLookup(id=uuid.uuid4(), vendor=vendor[:60], model=model[:150], model_key=key[:150], provider=PROVIDER, status="new",
                                 checked_at=datetime.min.replace(tzinfo=utcnow().tzinfo))
        db.add(row)
    return row


def _due(row: LifecycleWebLookup, now: datetime) -> bool:
    if row.status == "new":
        return True
    return now - row.checked_at >= (RETRY_ERRORS if row.status == "error" else REFRESH)


def _store(row: LifecycleWebLookup, found: list[dict], now: datetime) -> None:
    found.sort(key=lambda c: (c["confidence"] != "exact", c["name"]))
    row.candidates = found[:MAX_CANDIDATES]
    row.status = "found" if found else "not_found"
    row.error = None
    row.provider = PROVIDER
    row.checked_at = now


class RateLimited(Exception):
    pass


def _search(client, row: LifecycleWebLookup) -> list[dict]:
    params = {"q": row.query or row.model}
    if VENDOR_SLUGS.get(row.vendor):
        params["vendor"] = VENDOR_SLUGS[row.vendor]
    response = client.get("/api/v1/search", params=params)
    if response.status_code == 429:
        raise RateLimited()
    response.raise_for_status()
    return [candidate(item, row.query or row.model) for item in (response.json().get("data") or []) if item.get("category", "hardware") == "hardware"]


def _match(client, rows: list[LifecycleWebLookup]) -> dict:
    queries = [{"q": r.query or r.model, "ref": str(r.id), **({"vendor": VENDOR_SLUGS[r.vendor]} if VENDOR_SLUGS.get(r.vendor) else {})} for r in rows]
    response = client.post("/api/v1/match", json={"queries": queries})
    if response.status_code == 429:
        raise RateLimited()
    response.raise_for_status()
    found = {}
    for item in response.json().get("data") or []:
        ref = str(item.get("ref") or "")
        match = item.get("match") if item.get("matched") else None
        found[ref] = [candidate(match, str(item.get("query") or ""), item.get("confidence"))] if match else []
    return found


def lookup(db, rows: list[LifecycleWebLookup], now: datetime | None = None) -> dict:
    """Ask the web about ``rows``; returns counters.  Does not commit."""
    now = now or utcnow()
    stats = {"checked": 0, "found": 0, "not_found": 0, "errors": 0, "deferred": 0}
    if not rows:
        return stats
    keyed = bool(_api_key())
    todo = rows if keyed else rows[:KEYLESS_PER_RUN]
    stats["deferred"] = len(rows) - len(todo)
    with _client() as client:
        try:
            if keyed:
                for start in range(0, len(todo), BATCH):
                    chunk = todo[start:start + BATCH]
                    answers = _match(client, chunk)
                    for row in chunk:
                        _store(row, answers.get(str(row.id), []), now)
                        stats["checked"] += 1
                        stats[row.status] += 1
            else:
                for index, row in enumerate(todo):
                    try:
                        found = _search(client, row)
                    except RateLimited:
                        stats["deferred"] += len(todo) - index
                        break
                    except (httpx.HTTPError, ValueError) as exc:
                        row.status, row.error, row.checked_at = "error", type(exc).__name__[:255], now
                        stats["errors"] += 1
                        continue
                    _store(row, found, now)
                    stats["checked"] += 1
                    stats[row.status] += 1
        except RateLimited:
            stats["deferred"] += sum(1 for r in todo if r.checked_at != now)
        except (httpx.HTTPError, ValueError) as exc:
            for row in todo:
                if row.checked_at != now:
                    row.status, row.error, row.checked_at = "error", type(exc).__name__[:255], now
                    stats["errors"] += 1
    db.flush()
    return stats


def due_rows(db, now: datetime, force: bool = False) -> list[LifecycleWebLookup]:
    rows = [_row(db, vendor, model) for vendor, model in _pairs(db)]
    db.flush()
    return [r for r in rows if force or _due(r, now)]


def run(db, user, now: datetime | None = None) -> dict:
    """Operator-triggered lookup of every inventory model that is due (new, or not checked in 30 days); commits."""
    if not enabled():
        raise LifecycleWebError("disabled", "La ricerca web del ciclo di vita è disattivata (LIFECYCLE_WEB_LOOKUP=off).")
    now = now or utcnow()
    stats = lookup(db, due_rows(db, now), now)
    core.add_event(db, "LIFECYCLE_WEB_LOOKUP", actor=user, details={"provider": PROVIDER, **stats})
    db.commit()
    return stats


def lookup_one(db, user, lookup_id: uuid.UUID, query: str | None) -> LifecycleWebLookup:
    """Search one model again, optionally under another name (the SKU on the label); commits."""
    if not enabled():
        raise LifecycleWebError("disabled", "La ricerca web del ciclo di vita è disattivata (LIFECYCLE_WEB_LOOKUP=off).")
    row = db.get(LifecycleWebLookup, lookup_id)
    if row is None:
        raise HTTPException(404)
    text = re.sub(r"\s+", " ", str(query or "")).strip()[:150]
    if query is not None and text and len(text) < 2:
        raise LifecycleWebError("query", "Il nome da cercare deve avere almeno 2 caratteri.")
    if query is not None:
        row.query = text or None
    stats = lookup(db, [row])
    if stats["deferred"]:
        raise LifecycleWebError("rate_limited", "Troppe ricerche in un minuto: riprova tra poco.")
    core.add_event(db, "LIFECYCLE_WEB_LOOKUP", actor=user, details={"provider": PROVIDER, "model": row.model, "query": row.query, **stats})
    db.commit()
    return row


def record_from(row: LifecycleWebLookup, found: dict, today: date) -> dict:
    """The catalog record a web answer becomes.  A vendor status without a published date is dated on the day the source was read."""
    read = found.get("read_at") or today.isoformat()
    if read > today.isoformat():
        read = today.isoformat()
    eol, eos = found.get("end_of_sale"), found.get("end_of_updates")
    notes = []
    if found["status"] in ENDED_SALE and not eol:
        eol = read
        notes.append(f"Data di fine vendita non pubblicata dal vendor: risultava «{found['status']}» alla lettura della fonte del {read}.")
    if found["status"] in ENDED_UPDATES and not eos:
        eos = read
        notes.append(f"Data di fine aggiornamenti non pubblicata: risultava «{found['status']}» il {read}.")
    if eol and eos and eos < eol:
        eos = eol
    if found.get("replacement"):
        notes.append(f"Sostituto indicato dal vendor: {found['replacement']}.")
    if found.get("notes"):
        notes.append(found["notes"])
    aliases = [found["product_id"]] if found.get("product_id") and catalog.model_key(row.vendor, found["product_id"]) != row.model_key else []
    title = found.get("source_title") or found.get("vendor") or "vendor"
    return {
        "vendor": row.vendor, "model": row.model, "aliases": "|".join(aliases), "eol_date": eol or "", "eos_date": eos or "",
        "source": f"{PROVIDER} · {title}"[:160], "source_url": found.get("source_url") or found.get("page_url") or "",
        "evidence_date": read, "notes": " ".join(notes)[:2000],
    }


def apply(db, user, lookup_id: uuid.UUID, product_id: str) -> dict:
    """Write one web answer into the lifecycle catalog (new record, or the model's existing one); commits."""
    row = db.get(LifecycleWebLookup, lookup_id)
    if row is None:
        raise HTTPException(404)
    found = next((c for c in row.candidates or [] if c.get("product_id") == product_id), None)
    if found is None:
        raise LifecycleWebError("candidate", "Proposta non più disponibile: ripeti la ricerca.")
    raw = record_from(row, found, utcnow().date())
    existing = db.scalar(select(LifecycleRecord).where(LifecycleRecord.vendor == row.vendor, LifecycleRecord.model_key == row.model_key))
    record, linked, stats = catalog.save_record(db, user, raw, existing.id if existing else None)
    row = db.get(LifecycleWebLookup, lookup_id)
    row.applied_record_id, row.applied_product, row.applied_at = record.id, product_id[:150], utcnow()
    core.add_event(db, "LIFECYCLE_WEB_APPLIED", actor=user, details={
        "model": row.model, "vendor": row.vendor, "product": product_id, "confidence": found["confidence"], "status": found["status"],
        "eol": raw["eol_date"] or None, "eos": raw["eos_date"] or None, "source_url": raw["source_url"] or None, "record_id": str(record.id)})
    db.commit()
    return {"record_id": str(record.id), "linked": linked, "status_changed": stats.get("status_changed", 0)}


def apply_exact(db, user) -> dict:
    """Apply every exact match not applied yet (one per model); commits per model."""
    applied = skipped = 0
    for row in list(db.scalars(select(LifecycleWebLookup).where(LifecycleWebLookup.status == "found"))):
        exact = [c for c in row.candidates or [] if c.get("confidence") == "exact"]
        if len(exact) != 1 or row.applied_product == exact[0]["product_id"]:
            skipped += 1
            continue
        try:
            apply(db, user, row.id, exact[0]["product_id"])
            applied += 1
        except InputError:
            db.rollback()
            skipped += 1
    return {"applied": applied, "skipped": skipped}


def auto_lookup(now: datetime | None = None) -> dict:
    """Worker: new inventory models are looked up, the others re-checked every 30 days (keyless: a few per run)."""
    if not enabled():
        return {"disabled": True}
    now = now or utcnow()
    with SessionLocal() as db:
        stats = lookup(db, due_rows(db, now), now)
        db.commit()
        return stats


# ------------------------------------------------------------------- API --

def _lookup(row: LifecycleWebLookup, devices: int, record: LifecycleRecord | None) -> dict:
    return {
        "id": str(row.id), "vendor": row.vendor, "model": row.model, "query": row.query, "status": row.status, "error": row.error,
        "checked_at": row.checked_at.isoformat() if row.status != "new" else None, "devices": devices,
        "candidates": row.candidates or [], "applied_product": row.applied_product,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        "record": {"id": str(record.id), "model": record.model, "source": record.source} if record else None,
    }


def _fail(exc: InputError):
    raise HTTPException(422, {"code": exc.code, "message": str(exc.detail)}) from exc


@router.get("/lifecycle/web")
def web_state(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "security.read")
        pairs: dict = {}
        for v, m, n in db.execute(select(Device.vendor, Device.model, func.count(Device.id)).where(Device.model.is_not(None)).group_by(Device.vendor, Device.model)):
            key = (catalog.vendor_key(v), catalog.model_key(v, m))
            pairs[key] = pairs.get(key, 0) + n
        records = {(r.vendor, r.model_key): r for r in db.scalars(select(LifecycleRecord))}
        rows = [r for r in db.scalars(select(LifecycleWebLookup).order_by(LifecycleWebLookup.vendor, LifecycleWebLookup.model)) if (r.vendor, r.model_key) in pairs]
        known = {(r.vendor, r.model_key) for r in rows}
        return {
            "enabled": enabled(), "provider": PROVIDER, "provider_url": BASE_URL, "keyed": bool(_api_key()),
            "items": [_lookup(r, pairs[(r.vendor, r.model_key)], records.get((r.vendor, r.model_key))) for r in rows],
            "pending": sum(1 for key in pairs if key[1] and key not in known),
            "can_manage": core.has_permission(user, "lifecycle.manage"),
        }


@router.post("/lifecycle/web/run", dependencies=[Depends(require_csrf)])
def web_run(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "lifecycle.manage")
        try:
            return run(db, user)
        except InputError as exc:
            _fail(exc)


@router.post("/lifecycle/web/{lookup_id}/search", dependencies=[Depends(require_csrf)])
def web_search(request: Request, lookup_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "lifecycle.manage")
        try:
            row = lookup_one(db, user, lookup_id, body.get("query") if "query" in body else None)
        except InputError as exc:
            _fail(exc)
        return {"status": row.status, "candidates": row.candidates or []}


@router.post("/lifecycle/web/{lookup_id}/apply", dependencies=[Depends(require_csrf)])
def web_apply(request: Request, lookup_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "lifecycle.manage")
        try:
            return apply(db, user, lookup_id, str(body.get("product_id") or ""))
        except InputError as exc:
            _fail(exc)


@router.post("/lifecycle/web/apply-exact", dependencies=[Depends(require_csrf)])
def web_apply_exact(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "lifecycle.manage")
        return apply_exact(db, user)


def install_lifecycle_web(app) -> None:
    app.include_router(router)
