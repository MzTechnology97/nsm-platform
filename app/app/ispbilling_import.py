"""Import of customers and sites from the ISPBilling mirror (ISPB-01), administrators only.

- ``GET /api/ui/v1/integrations/ispbilling/customers``: mirror rows with their NSM state — ``linked`` (already an
  NSM customer), ``linkable`` (an NSM customer with the same code, or the same name, exists and is not linked to
  another ISPBilling customer), ``new`` — filters by state, text, ISPBilling status and group;
- ``POST .../preview``: what a commit would do for the selected customers (create or link, sites to add);
- ``POST .../commit``: creates or links the customers and adds their sites (installation addresses; the
  registered address when the customer has none). Never renames, moves or deletes anything already in NSM;
  a site whose address is already present on that NSM customer is linked instead of duplicated. Audited
  (``ISPBILLING_CUSTOMERS_IMPORTED``).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from app import main as core
from app.db import SessionLocal
from app.ispbilling_connector import connection_row, normalized, site_name
from app.ispbilling_models import IspbillingAddress, IspbillingCustomer
from app.models import Customer, Site, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1/integrations/ispbilling", tags=["ui"])
PERMISSION = "users.manage"
STATES = ("new", "linkable", "linked", "all")
PAGE = 100
MAX_SELECTION = 300


def _context(db):
    mirror = list(db.scalars(select(IspbillingCustomer).order_by(IspbillingCustomer.name)))
    addresses: dict[str, list[IspbillingAddress]] = {}
    for address in db.scalars(select(IspbillingAddress).order_by(IspbillingAddress.label, IspbillingAddress.address)):
        addresses.setdefault(address.customer_external_id, []).append(address)
    customers = {c.id: c for c in db.scalars(select(Customer))}
    linked_ids = {m.nsm_customer_id for m in mirror if m.nsm_customer_id in customers}
    by_code = {str(c.code).strip().lower(): c for c in customers.values() if c.code}
    by_name: dict[str, list[Customer]] = {}
    for customer in customers.values():
        by_name.setdefault(normalized(customer.name), []).append(customer)
    return mirror, addresses, customers, linked_ids, by_code, by_name


def _match(item, customers, linked_ids, by_code, by_name):
    """(state, NSM customer, reason) for one mirror row."""
    if item.nsm_customer_id in customers:
        return "linked", customers[item.nsm_customer_id], "code"
    candidate = by_code.get(str(item.internal_code or "").strip().lower()) if item.internal_code else None
    if candidate is not None and candidate.id not in linked_ids:
        return "linkable", candidate, "code"
    named = [c for c in by_name.get(normalized(item.name), []) if c.id not in linked_ids]
    if len(named) == 1:
        return "linkable", named[0], "name"
    return "new", None, None


def _site_plan(item, addresses, customer: Customer | None):
    """Sites of a mirror customer: (address row or None for the registered address, name, address, existing NSM site)."""
    rows = addresses.get(item.external_id, [])
    wanted = [(a, site_name(a.label, a.address), a.address) for a in rows]
    if not wanted and item.address:
        wanted = [(None, "Sede principale", item.address)]
    existing = {normalized(s.address): s for s in (customer.sites if customer else []) if s.address}
    plan = []
    for row, name, address in wanted:
        site = None
        if row is not None and row.nsm_site_id and customer:
            site = next((s for s in customer.sites if s.id == row.nsm_site_id), None)
        plan.append((row, name, address, site or existing.get(normalized(address))))
    return plan


def _customer_ref(customer):
    return {"id": str(customer.id), "name": customer.name, "code": customer.code} if customer else None


def _row(item, state, customer, reason, addresses):
    plan = _site_plan(item, addresses, customer)
    return {
        "external_id": item.external_id, "code": item.internal_code, "name": item.name, "kind": item.kind, "status": item.status,
        "group": item.group_name, "state": state, "match": reason, "customer": _customer_ref(customer),
        "sites": [{"name": name, "address": address, "site": {"id": str(site.id), "name": site.name} if site else None} for _row_, name, address, site in plan],
    }


@router.get("/customers")
def customers(request: Request, state: str = "new", q: str = "", status: str = "", group: str = "", offset: int = 0):
    with SessionLocal() as db:
        ui_user(request, db, PERMISSION)
        state = state if state in STATES else "new"
        mirror, addresses, nsm, linked_ids, by_code, by_name = _context(db)
        text = q.strip().lower()[:100]
        counts = {key: 0 for key in STATES if key != "all"}
        rows = []
        for item in mirror:
            if status and item.status != status:
                continue
            if group and item.group_name != group:
                continue
            if text and text not in f"{item.name} {item.internal_code or ''} {item.address or ''}".lower():
                continue
            row_state, customer, reason = _match(item, nsm, linked_ids, by_code, by_name)
            counts[row_state] += 1
            if state in ("all", row_state):
                rows.append((item, row_state, customer, reason))
        offset = max(0, int(offset or 0))
        connection = connection_row(db)
        return jsonable_encoder({
            "configured": connection is not None, "last_sync_at": connection.last_sync_at if connection else None,
            "details_pending": ((connection.settings or {}).get("last_sync") or {}).get("details_pending") if connection else None,
            "counts": counts, "total": len(rows), "offset": offset, "limit": PAGE,
            "statuses": sorted({m.status for m in mirror if m.status}), "groups": sorted({m.group_name for m in mirror if m.group_name}),
            "items": [_row(item, row_state, customer, reason, addresses) for item, row_state, customer, reason in rows[offset:offset + PAGE]],
        })


def _selection(body: dict) -> list[str]:
    ids = [str(value)[:40] for value in body.get("external_ids") or [] if str(value).strip()]
    if not ids:
        raise HTTPException(422, {"code": "empty", "message": "Seleziona almeno un cliente."})
    if len(ids) > MAX_SELECTION:
        raise HTTPException(422, {"code": "too_many", "message": f"Importa al massimo {MAX_SELECTION} clienti alla volta."})
    return list(dict.fromkeys(ids))


def _plan(db, ids):
    mirror, addresses, nsm, linked_ids, by_code, by_name = _context(db)
    wanted = {m.external_id: m for m in mirror if m.external_id in set(ids)}
    used_codes = {str(c.code).strip().lower() for c in nsm.values() if c.code}
    plan = []
    for external in ids:
        item = wanted.get(external)
        if item is None:
            plan.append({"external_id": external, "action": "skip", "reason": "Cliente non più presente in ISPBilling: aggiorna i dati."})
            continue
        state, customer, reason = _match(item, nsm, linked_ids, by_code, by_name)
        sites = _site_plan(item, addresses, customer)
        code = item.internal_code if item.internal_code and item.internal_code.strip().lower() not in used_codes else None
        entry = {"external_id": external, "name": item.name, "code": item.internal_code, "state": state, "match": reason,
                 "action": "create" if state == "new" else "link" if state == "linkable" else "update",
                 "customer": _customer_ref(customer), "code_in_use": state == "new" and item.internal_code and code is None,
                 "sites_new": [{"name": name, "address": address} for _r, name, address, site in sites if site is None],
                 "sites_existing": [{"name": site.name, "address": address} for _r, _n, address, site in sites if site is not None],
                 "_item": item, "_customer": customer, "_sites": sites, "_code": code}
        if state == "new" and code:
            used_codes.add(code.strip().lower())
        if state == "linkable":
            linked_ids.add(customer.id)  # one NSM customer is linked to one ISPBilling customer only
        plan.append(entry)
    return plan


def _public(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if not key.startswith("_")}


@router.post("/preview", dependencies=[Depends(require_csrf)])
def preview(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        ui_user(request, db, PERMISSION)
        return jsonable_encoder({"plan": [_public(entry) for entry in _plan(db, _selection(body))]})


@router.post("/commit", dependencies=[Depends(require_csrf)])
def commit(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, PERMISSION)
        now = utcnow()
        result = {"created": 0, "linked": 0, "sites_created": 0, "sites_linked": 0, "skipped": []}
        for entry in _plan(db, _selection(body)):
            if entry["action"] == "skip":
                result["skipped"].append({"external_id": entry["external_id"], "reason": entry["reason"]})
                continue
            item, customer = entry["_item"], entry["_customer"]
            if customer is None:
                customer = Customer(id=uuid.uuid4(), name=item.name, code=entry["_code"], notes=f"Importato da ISPBilling (cliente {item.external_id}).")
                db.add(customer)
                db.flush()
                result["created"] += 1
            elif entry["action"] == "link":
                result["linked"] += 1
            if item.nsm_customer_id != customer.id:
                item.nsm_customer_id, item.linked_at = customer.id, now
            for row, name, address, site in entry["_sites"]:
                if site is None:
                    site = Site(id=uuid.uuid4(), customer_id=customer.id, name=name, address=address)
                    db.add(site)
                    db.flush()  # the mirror row points to it: insert it first
                    result["sites_created"] += 1
                elif row is not None and row.nsm_site_id != site.id:
                    result["sites_linked"] += 1
                if row is not None:
                    row.nsm_site_id = site.id
            db.flush()
        core.add_event(db, "ISPBILLING_CUSTOMERS_IMPORTED", actor=user, source="portal",
                       details={key: value for key, value in result.items() if key != "skipped"} | {"skipped": len(result["skipped"])})
        db.commit()
        result["imported_customers"] = int(db.scalar(select(func.count()).select_from(IspbillingCustomer).where(IspbillingCustomer.nsm_customer_id.is_not(None))) or 0)
        return result


def install_ispbilling_import(app) -> None:
    app.include_router(router)
