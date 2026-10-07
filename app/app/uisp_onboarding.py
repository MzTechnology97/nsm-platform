"""Bulk onboarding of UISP devices into NSM (UBNT-03).

The operator picks UISP devices that are not in NSM yet, chooses the NSM
Customer (and optional Site) explicitly, reviews a preview and confirms.  NSM
never derives the Customer/Site from UISP metadata.  At confirmation the UISP
list is fetched again, so a device removed or changed in the meantime is
reported instead of being created from stale data.  Devices already present
with the same MAC but not yet linked are associated, never duplicated.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import main as core
from app import uisp_connector as uisp
from app.db import SessionLocal
from app.models import Customer, Device, Site, utcnow
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
PAGE_SIZE = 100
MAX_BATCH = 200
ROLE_TYPES = {
    "station": "wireless_cpe",
    "ap": "wireless_ap",
    "router": "router",
    "gateway": "router",
    "switch": "switch",
    "olt": "olt",
    "onu": "ont",
}


def device_type_for(candidate: dict) -> str:
    role = str(candidate.get("role") or "").lower()
    if role in ROLE_TYPES:
        return ROLE_TYPES[role]
    if str(candidate.get("category") or "").lower() == "optical":
        return "ont"
    return "other"


def classify(db, candidates: list[dict]) -> list[dict]:
    """Attach the NSM state to every UISP candidate."""
    linked = {d.external_device_id: d for d in db.scalars(select(Device).where(Device.vendor == "ubiquiti", Device.external_device_id.is_not(None)))}
    by_mac = {}
    for device in db.scalars(select(Device).where(Device.primary_mac.is_not(None))):
        by_mac.setdefault(device.primary_mac, []).append(device)
    mac_counts = {}
    for c in candidates:
        if c.get("primary_mac"):
            mac_counts[c["primary_mac"]] = mac_counts.get(c["primary_mac"], 0) + 1
    rows = []
    for c in candidates:
        state, device, reason = "new", None, None
        if not c.get("external_id"):
            state, reason = "blocked", "UISP non espone un identificativo stabile."
        elif c["external_id"] in linked:
            state, device = "linked", linked[c["external_id"]]
        elif not c.get("primary_mac"):
            state, reason = "blocked", "MAC assente in UISP."
        elif mac_counts.get(c["primary_mac"], 0) > 1:
            state, reason = "blocked", "MAC presente su più dispositivi UISP."
        else:
            same_mac = by_mac.get(c["primary_mac"], [])
            ubiquiti = [d for d in same_mac if d.vendor == "ubiquiti" and not d.external_device_id]
            if len(same_mac) > 1 or (same_mac and not ubiquiti):
                state, reason = "blocked", "MAC già usato da un altro apparato NSM: verifica il record."
            elif ubiquiti:
                state, device = "linkable", ubiquiti[0]
        rows.append({**c, "state": state, "device": device, "reason": reason, "device_type": device_type_for(c)})
    return rows


def _filter(rows, q: str, uisp_site: str, state: str):
    term = q.strip().lower()
    out = []
    for row in rows:
        if state and row["state"] != state:
            continue
        if uisp_site and (row.get("site") or {}).get("name") != uisp_site:
            continue
        if term:
            hay = " ".join(str(row.get(k) or "") for k in ("device_identity", "model", "primary_mac", "management_ip", "serial_number")).lower()
            hay += " " + str((row.get("site") or {}).get("name") or "").lower()
            if term not in hay:
                continue
        out.append(row)
    return out


def _load(db, user):
    if not core.has_permission(user, "devices.write"):
        raise HTTPException(403)
    connection = uisp._connection(db, enabled_only=True)
    return connection, classify(db, uisp._fetch_candidates(connection))


@router.get("/integrations/uisp/onboarding", response_class=HTMLResponse, name="uisp_onboarding")
def onboarding(request: Request, q: str = "", uisp_site: str = "", state: str = "new", customer: str = "", page: int = 1):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        error, rows = None, []
        try:
            _, rows = _load(db, user)
        except uisp.UispConnectorError as exc:
            error = str(exc)
        counts = {key: sum(1 for r in rows if r["state"] == key) for key in ("new", "linkable", "linked", "blocked")}
        sites = sorted({(r.get("site") or {}).get("name") for r in rows if (r.get("site") or {}).get("name")})
        filtered = _filter(rows, q, uisp_site, state)
        pages = max(1, -(-len(filtered) // PAGE_SIZE))
        page = min(max(1, page), pages)
        customers = list(db.scalars(select(Customer).order_by(Customer.name)))
        selected_customer = None
        try:
            selected_customer = db.get(Customer, uuid.UUID(customer)) if customer else None
        except ValueError:
            selected_customer = None
        return core.render(
            request, db, user, "uisp_onboarding.html", title="Onboarding UISP",
            rows=filtered[(page - 1) * PAGE_SIZE: page * PAGE_SIZE], total=len(filtered), page=page, pages=pages,
            counts=counts, uisp_sites=sites, q=q, uisp_site=uisp_site, state=state, error=error,
            customers=customers, selected_customer=selected_customer,
            all_sites=list(db.scalars(select(Site).order_by(Site.name))), max_batch=MAX_BATCH,
        )


def _selection(form) -> list[str]:
    return [str(v) for v in form.getlist("external_id") if str(v).strip()]


def _target(db, form):
    try:
        customer = db.get(Customer, uuid.UUID(str(form.get("customer_id") or "")))
    except ValueError:
        customer = None
    if not customer:
        raise ValueError("Seleziona il cliente NSM a cui assegnare i nuovi apparati.")
    site = None
    if form.get("site_id"):
        try:
            site = db.get(Site, uuid.UUID(str(form.get("site_id"))))
        except ValueError:
            site = None
        if not site or site.customer_id != customer.id:
            raise ValueError("La sede scelta non appartiene al cliente.")
    return customer, site


@router.post("/integrations/uisp/onboarding/preview", response_class=HTMLResponse, name="uisp_onboarding_preview")
async def onboarding_preview(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    back = "/integrations/uisp/onboarding"
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        selected = _selection(form)
        try:
            if not selected:
                raise ValueError("Seleziona almeno un dispositivo UISP.")
            if len(selected) > MAX_BATCH:
                raise ValueError(f"Massimo {MAX_BATCH} dispositivi per operazione.")
            customer, site = _target(db, form)
            _, rows = _load(db, user)
        except (ValueError, uisp.UispConnectorError) as exc:
            return flash_redirect(request, back, "warning", str(exc), title="Anteprima non disponibile")
        by_id = {r["external_id"]: r for r in rows if r.get("external_id")}
        plan = []
        for external_id in selected:
            row = by_id.get(external_id)
            if not row:
                plan.append({"external_id": external_id, "action": "missing", "reason": "Non più presente in UISP."})
            elif row["state"] in ("new", "linkable"):
                plan.append({**row, "action": "create" if row["state"] == "new" else "link"})
            else:
                plan.append({**row, "action": "skip", "reason": row.get("reason") or "Già associato in NSM."})
        return core.render(request, db, user, "uisp_onboarding_preview.html", title="Anteprima onboarding UISP",
                           plan=plan, customer=customer, site=site)


@router.post("/integrations/uisp/onboarding/commit", name="uisp_onboarding_commit")
async def onboarding_commit(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    back = "/integrations/uisp/onboarding"
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.write")
        selected = _selection(form)
        try:
            if not selected or len(selected) > MAX_BATCH:
                raise ValueError(f"Selezione vuota o oltre {MAX_BATCH} dispositivi.")
            customer, site = _target(db, form)
            _, rows = _load(db, user)  # fresh UISP data, never the preview page
        except (ValueError, uisp.UispConnectorError) as exc:
            return flash_redirect(request, back, "warning", str(exc), title="Onboarding non eseguito")
        by_id = {r["external_id"]: r for r in rows if r.get("external_id")}
        created = linked = 0
        skipped = []
        for external_id in selected:
            row = by_id.get(external_id)
            if not row or row["state"] not in ("new", "linkable"):
                skipped.append((row or {}).get("device_identity") or external_id)
                continue
            if row["state"] == "new":
                device = Device(
                    customer_id=customer.id, site_id=site.id if site else None, vendor="ubiquiti",
                    device_type=row["device_type"], name=row.get("device_identity") or f"UISP {row['primary_mac']}",
                    primary_mac=row["primary_mac"], management_source="uisp", status="pending_link",
                )
                db.add(device)
                db.flush()
                core.add_event(db, "DEVICE_ADDED", actor=user, customer_id=customer.id, device_id=device.id,
                               details={"vendor": "ubiquiti", "management_source": "uisp", "source": "uisp_bulk_onboarding"})
                created += 1
            else:
                device = row["device"]
                linked += 1
            uisp.apply_candidate(db, device, row, user, event_type="UISP_DEVICE_ASSOCIATED")
        customer_id = customer.id
        core.add_event(db, "UISP_BULK_ONBOARDING", actor=user, customer_id=customer.id,
                       details={"created": created, "linked": linked, "skipped": len(skipped), "site_id": str(site.id) if site else None})
        db.commit()
    message = f"{created} apparati creati e {linked} associati da UISP."
    if skipped:
        message += f" {len(skipped)} saltati perché cambiati in UISP o già presenti: {', '.join(map(str, skipped[:5]))}."
    return flash_redirect(request, f"/customers/{customer_id}/devices", "success", message, title="Onboarding UISP completato")


def install_uisp_onboarding(app) -> None:
    app.include_router(router)
