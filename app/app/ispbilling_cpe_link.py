"""ISPBilling account → NSM device: customer and site proposed for confirmation (operator decision 2026-10-23).

``ispbilling_radius.match_devices`` finds the device of each RADIUS account (PPPoE user of a MikroTik or of a TR-069
CPE, static IP, caller-id MAC or address of the active session on a concentrator in NSM). This module compares the
device's customer and site with the account's ISPBilling customer and installation address, as imported in NSM:

- ``linked`` — same customer and same site (or the address has no NSM site): nothing to do;
- ``site`` — same customer, another or no site: *proposal* to set the site;
- ``conflict`` — the device belongs to another NSM customer: never changed by itself, shown to check; an operator can
  still reassign it from the device page with an explicit confirmation;
- ``not_imported`` — the ISPBilling customer is not imported in NSM yet (*Integrazioni → ISPBilling → Importa*).

Nothing is applied without an operator: ``POST /devices/{id}/ispbilling/link`` (``devices.write``) applies one
proposal, audited ``ISPBILLING_DEVICE_LINKED`` (previous and new customer/site); *Ignora* stores the account on the
device (``inventory_data["ispbilling_link"]["dismissed"]``, ``ISPBILLING_LINK_DISMISSED``) so it is not proposed
again. ``GET /integrations/ispbilling/links`` lists proposals and conflicts.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app.db import SessionLocal
from app.ispbilling_models import IspbillingAddress, IspbillingCustomer, IspbillingRadiusAccount
from app.models import Customer, Device, Site
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
PROPOSALS = ("site", "conflict")
MAX_ROWS = 500


def _dismissed(device) -> set:
    return set(((device.inventory_data or {}).get("ispbilling_link") or {}).get("dismissed") or [])


def link_state(device, account, customer: IspbillingCustomer | None, address: IspbillingAddress | None) -> dict:
    """What NSM proposes for one matched account (see the module docstring)."""
    target_customer = customer.nsm_customer_id if customer else None
    target_site = address.nsm_site_id if address and address.customer_external_id == account.customer_external_id else None
    if target_customer is None:
        state = "not_imported"
    elif device.customer_id != target_customer:
        state = "conflict"
    elif target_site and device.site_id != target_site:
        state = "site"
    else:
        state = "linked"
    return {"state": state, "customer_id": str(target_customer) if target_customer else None, "site_id": str(target_site) if target_site else None,
            "dismissed": account.external_id in _dismissed(device)}


def _lookups(db, accounts):
    customers = {c.external_id: c for c in db.scalars(select(IspbillingCustomer).where(
        IspbillingCustomer.external_id.in_([a.customer_external_id for a in accounts if a.customer_external_id] or [""])))}
    addresses = {a.external_id: a for a in db.scalars(select(IspbillingAddress).where(
        IspbillingAddress.external_id.in_([a.address_external_id for a in accounts if a.address_external_id] or [""])))}
    return customers, addresses


def _names(db, ids, model) -> dict:
    ids = [uuid.UUID(i) for i in ids if i]
    return {str(row.id): row.name for row in db.scalars(select(model).where(model.id.in_(ids or [uuid.uuid4()])))}


def device_links(db, device) -> dict[str, dict]:
    """Link state per account external id, for the device page."""
    accounts = list(db.scalars(select(IspbillingRadiusAccount).where(IspbillingRadiusAccount.nsm_device_id == device.id)))
    customers, addresses = _lookups(db, accounts)
    states = {a.external_id: link_state(device, a, customers.get(a.customer_external_id or ""), addresses.get(a.address_external_id or ""))
              for a in accounts}
    customer_names = _names(db, {s["customer_id"] for s in states.values()}, Customer)
    site_names = _names(db, {s["site_id"] for s in states.values()}, Site)
    for state in states.values():
        state["customer"] = customer_names.get(state["customer_id"] or "")
        state["site"] = site_names.get(state["site_id"] or "")
    return states


def proposals(db) -> dict:
    """Proposals and conflicts over all matched accounts (not dismissed)."""
    accounts = list(db.scalars(select(IspbillingRadiusAccount).where(IspbillingRadiusAccount.nsm_device_id.is_not(None))))
    customers, addresses = _lookups(db, accounts)
    devices = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_([a.nsm_device_id for a in accounts] or [uuid.uuid4()])))}
    counts = {"linked": 0, "site": 0, "conflict": 0, "not_imported": 0, "dismissed": 0}
    rows = []
    for account in accounts:
        device = devices.get(account.nsm_device_id)
        if device is None:
            continue
        state = link_state(device, account, customers.get(account.customer_external_id or ""), addresses.get(account.address_external_id or ""))
        if state["dismissed"] and state["state"] in PROPOSALS:
            counts["dismissed"] += 1
            continue
        counts[state["state"]] += 1
        if state["state"] in PROPOSALS and len(rows) < MAX_ROWS:
            rows.append({"account": account.external_id, "username": account.username, "matched_by": account.matched_by, **state,
                         "device": {"id": str(device.id), "name": device.display_name or device.device_identity or device.name,
                                    "customer_id": str(device.customer_id), "site_id": str(device.site_id) if device.site_id else None}})
    names_c = _names(db, {r["customer_id"] for r in rows} | {r["device"]["customer_id"] for r in rows}, Customer)
    names_s = _names(db, {r["site_id"] for r in rows} | {r["device"]["site_id"] for r in rows}, Site)
    for row in rows:
        row["customer"], row["site"] = names_c.get(row["customer_id"] or ""), names_s.get(row["site_id"] or "")
        row["device"]["customer"], row["device"]["site"] = names_c.get(row["device"]["customer_id"]), names_s.get(row["device"]["site_id"] or "")
    rows.sort(key=lambda r: (r["state"] != "site", (r["device"]["name"] or "").lower()))
    return {"counts": counts, "rows": rows}


def apply_link(db, user, device, account, reassign: bool = False) -> dict:
    """Apply one proposal (audited). A device of another customer needs ``reassign``. Caller commits."""
    customers, addresses = _lookups(db, [account])
    state = link_state(device, account, customers.get(account.customer_external_id or ""), addresses.get(account.address_external_id or ""))
    if state["state"] == "not_imported":
        raise HTTPException(409, {"code": "not_imported", "message": "Il cliente ISPBilling non è ancora importato in NSM: importalo prima."})
    if state["state"] == "linked":
        return state
    if state["state"] == "conflict" and not reassign:
        raise HTTPException(409, {"code": "conflict", "message": "L'apparato è di un altro cliente NSM: conferma la riassegnazione."})
    before = {"customer_id": str(device.customer_id), "site_id": str(device.site_id) if device.site_id else None}
    device.customer_id = uuid.UUID(state["customer_id"])
    if state["site_id"]:
        device.site_id = uuid.UUID(state["site_id"])
    elif state["state"] == "conflict":
        device.site_id = None  # the old site belongs to the old customer
    core.add_event(db, "ISPBILLING_DEVICE_LINKED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                   details={"account": account.external_id, "matched_by": account.matched_by, "kind": state["state"], "before": before,
                            "after": {"customer_id": state["customer_id"], "site_id": str(device.site_id) if device.site_id else None}})
    return {**state, "state": "linked"}


def _account(db, device, external_id: str):
    account = db.scalar(select(IspbillingRadiusAccount).where(IspbillingRadiusAccount.external_id == str(external_id or "")[:40],
                                                               IspbillingRadiusAccount.nsm_device_id == device.id))
    if account is None:
        raise HTTPException(404, {"code": "no_account", "message": "Account ISPBilling non abbinato a questo apparato."})
    return account


@router.get("/integrations/ispbilling/links")
def ispbilling_links(request: Request):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.read")
        return {**proposals(db), "can_apply": core.has_permission(user, "devices.write")}


@router.post("/devices/{device_id}/ispbilling/link", dependencies=[Depends(require_csrf)])
def device_ispbilling_link(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = db.get(Device, device_id)
        if device is None:
            raise HTTPException(404)
        result = apply_link(db, user, device, _account(db, device, (body or {}).get("account")), bool((body or {}).get("reassign")))
        db.commit()
        return result


@router.post("/devices/{device_id}/ispbilling/dismiss", dependencies=[Depends(require_csrf)])
def device_ispbilling_dismiss(request: Request, device_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "devices.write")
        device = db.get(Device, device_id)
        if device is None:
            raise HTTPException(404)
        account = _account(db, device, (body or {}).get("account"))
        data = dict(device.inventory_data or {})
        link = dict(data.get("ispbilling_link") or {})
        link["dismissed"] = sorted(set(link.get("dismissed") or []) | {account.external_id})
        data["ispbilling_link"] = link
        device.inventory_data = data
        core.add_event(db, "ISPBILLING_LINK_DISMISSED", actor=user, customer_id=device.customer_id, device_id=device.id, source="portal",
                       details={"account": account.external_id})
        db.commit()
        return {"dismissed": True}


def install_ispbilling_cpe_link(app) -> None:
    app.include_router(router)
