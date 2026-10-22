"""Bulk onboarding of Cambium devices from cnMaestro into NSM (VEND-02), like the UISP one (UBNT-03).

The operator picks cnMaestro devices that are not in NSM yet, chooses the NSM
Customer (and optional Site) explicitly, reviews a preview and confirms. NSM
never derives the Customer/Site from cnMaestro networks or towers. At
confirmation the cnMaestro list is read again, so a device removed or changed
in the meantime is reported instead of being created from stale data. A
Cambium device already in NSM with the same MAC but not yet linked is
associated, never duplicated. cnMaestro identifies devices by MAC, which is
the external id used here.

- ``GET /api/ui/v1/integrations/cnmaestro/onboarding`` (``devices.write``):
  cnMaestro devices with their NSM state (``new``, ``linkable``, ``linked``,
  ``blocked`` with the reason), filters by text, network/tower and state;
- ``POST .../preview`` and ``.../commit`` with ``external_ids`` (MACs),
  ``customer_id`` and optional ``site_id``.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from app import cnmaestro_connector as cnm
from app import connector_onboarding
from app import main as core
from app.db import SessionLocal
from app.models import Customer, Device, Site
from app.ui_api import brand_icon, require_csrf, ui_user
from app.ui_incidents import device_ref
from app.uisp_onboarding import target

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
PAGE_SIZE = 100
MAX_BATCH = 200
STATES = ("new", "linkable", "linked", "blocked")


def device_type_for(row: dict) -> str:
    """NSM device type from the cnMaestro product family and mode; ``other`` when they do not say."""
    kind = str(row.get("type") or "").lower()
    mode = str(row.get("mode") or "").lower()
    if "matrix" in kind:
        return "switch"
    if "wifi" in kind or "cnpilot" in kind:
        return "wireless_ap"
    if mode in ("ap", "access point", "access_point", "master", "bhm", "ptp master"):
        return "wireless_ap"
    if mode in ("sm", "sta", "station", "subscriber", "client", "slave", "bhs"):
        return "wireless_cpe"
    return "other"


def _candidates(row) -> list[dict]:
    client = cnm.client_for(row)
    try:
        out = []
        for raw in client.paged("/devices", "l'elenco apparati"):
            candidate = cnm.candidate(raw)
            candidate["mode"] = str(raw.get("mode") or raw.get("network_mode") or "").strip() or None
            candidate["group"] = str(raw.get("tower") or raw.get("site") or raw.get("network") or "").strip() or None
            out.append(candidate)
        return out
    finally:
        client.close()


def _cambium(device) -> bool:
    return cnm.is_cambium(device) or device.vendor == "cambium"


def classify(db, candidates: list[dict]) -> list[dict]:
    """Attach the NSM state to every cnMaestro device."""
    every = {"include_decommissioned": True}  # a decommissioned device is still in NSM: never import it twice
    devices = list(db.scalars(select(Device).where(Device.primary_mac.is_not(None) | Device.inventory_data.is_not(None)).execution_options(**every)))
    linked = {}
    by_mac: dict = {}
    for device in devices:
        mac = ((device.inventory_data or {}).get("cnmaestro") or {}).get("mac")
        if mac:
            linked[mac] = device
        if device.primary_mac:
            by_mac.setdefault(device.primary_mac, []).append(device)
    counts: dict = {}
    for candidate in candidates:
        if candidate.get("mac"):
            counts[candidate["mac"]] = counts.get(candidate["mac"], 0) + 1
    rows = []
    for candidate in candidates:
        mac = candidate.get("mac")
        state, device, reason = "new", None, None
        if not mac:
            state, reason = "blocked", "cnMaestro non espone un MAC valido."
        elif counts.get(mac, 0) > 1:
            state, reason = "blocked", "MAC presente su più apparati cnMaestro."
        elif mac in linked and linked[mac].decommissioned_at is not None:
            state, reason = "blocked", "Apparato disattivato in NSM: riattivalo per riprendere la sincronizzazione."
        elif mac in linked:
            state, device = "linked", linked[mac]
        else:
            same = by_mac.get(mac, [])
            cambium = [d for d in same if _cambium(d)]
            if len(same) > 1 or (same and not cambium):
                state, reason = "blocked", "MAC già usato da un altro apparato NSM: verifica il record."
            elif cambium and cambium[0].decommissioned_at is not None:
                state, reason = "blocked", "Apparato disattivato in NSM: riattivalo prima di associarlo."
            elif cambium:
                state, device = "linkable", cambium[0]
        rows.append({**candidate, "external_id": mac, "state": state, "device": device, "reason": reason, "device_type": device_type_for(candidate)})
    return rows


def _filter(rows, q: str, group: str, state: str):
    term = q.strip().lower()
    out = []
    for row in rows:
        if state and row["state"] != state:
            continue
        if group and row.get("group") != group:
            continue
        if term:
            hay = " ".join(str(row.get(k) or "") for k in ("name", "model", "mac", "ip", "serial", "group")).lower()
            if term not in hay:
                continue
        out.append(row)
    return out


def _load(db, user):
    if not core.has_permission(user, "devices.write"):
        raise HTTPException(403)
    row = cnm.connection_row(db)
    if row is None or not row.is_enabled:
        raise cnm.CnMaestroError("Il connettore cnMaestro non è configurato o è disabilitato.")
    return classify(db, _candidates(row))


def preview_plan(db, user, selected: list[str], customer_id, site_id):
    """What would happen to each selected cnMaestro device, from fresh data. (customer, site, plan); ValueError or ``CnMaestroError``."""
    if not selected:
        raise ValueError("Seleziona almeno un apparato cnMaestro.")
    if len(selected) > MAX_BATCH:
        raise ValueError(f"Massimo {MAX_BATCH} apparati per operazione.")
    customer, site = target(db, customer_id, site_id)
    by_id = {r["external_id"]: r for r in _load(db, user) if r.get("external_id")}
    plan = []
    for external_id in selected:
        row = by_id.get(external_id)
        if not row:
            plan.append({"external_id": external_id, "action": "missing", "reason": "Non più presente in cnMaestro."})
        elif row["state"] in ("new", "linkable"):
            plan.append({**row, "action": "create" if row["state"] == "new" else "link"})
        else:
            plan.append({**row, "action": "skip", "reason": row.get("reason") or "Già associato in NSM."})
    return customer, site, plan


def commit_selection(db, user, selected: list[str], customer_id, site_id) -> dict:
    """Create or link the selected devices from fresh cnMaestro data, audited. Caller commits."""
    if not selected or len(selected) > MAX_BATCH:
        raise ValueError(f"Selezione vuota o oltre {MAX_BATCH} apparati.")
    customer, site = target(db, customer_id, site_id)
    by_id = {r["external_id"]: r for r in _load(db, user) if r.get("external_id")}  # fresh data, never the preview page
    created = linked = 0
    skipped = []
    for external_id in selected:
        row = by_id.get(external_id)
        if not row or row["state"] not in ("new", "linkable"):
            skipped.append((row or {}).get("name") or external_id)
            continue
        if row["state"] == "new":
            device = Device(customer_id=customer.id, site_id=site.id if site else None, vendor="cambium", device_type=row["device_type"],
                            name=row.get("name") or f"cnMaestro {row['mac']}", primary_mac=row["mac"], management_source="cnmaestro",
                            status="pending_link")
            db.add(device)
            db.flush()
            core.add_event(db, "DEVICE_ADDED", actor=user, customer_id=customer.id, device_id=device.id,
                           details={"vendor": "cambium", "management_source": "cnmaestro", "source": "cnmaestro_bulk_onboarding"})
            created += 1
        else:
            device = row["device"]
            linked += 1
        connector_onboarding.link(db, device, "cambium", row, user)
    core.add_event(db, "CNMAESTRO_BULK_ONBOARDING", actor=user, customer_id=customer.id,
                   details={"created": created, "linked": linked, "skipped": len(skipped), "site_id": str(site.id) if site else None})
    return {"customer_id": customer.id, "created": created, "linked": linked, "skipped": skipped}


def _cambium_brand() -> dict:
    return brand_icon(Device(vendor="cambium", inventory_data={}))


def _row(row: dict) -> dict:
    """A cnMaestro device for the interface, in the same shape as the UISP onboarding rows."""
    return {"external_id": row.get("external_id"), "device_identity": row.get("name"), "model": row.get("model"), "primary_mac": row.get("mac"),
            "management_ip": row.get("ip"), "firmware_version": row.get("firmware"), "site": row.get("group"), "status": row.get("status"),
            "device_type": row.get("device_type"), "state": row.get("state"), "action": row.get("action"), "reason": row.get("reason"),
            "brand": _cambium_brand(), "device": device_ref(row.get("device"))}


def _user(request: Request, db):
    return ui_user(request, db, "devices.write")


def _selection(body: dict) -> list[str]:
    raw = body.get("external_ids")
    return [value.strip() for value in raw if isinstance(value, str) and value.strip()] if isinstance(raw, list) else []


def _text(body: dict, key: str) -> str:
    value = body.get(key)
    return value.strip() if isinstance(value, str) else ""


@router.get("/integrations/cnmaestro/onboarding")
def onboarding_list(request: Request, q: str = "", group: str = "", state: str = "new", offset: int = 0):
    with SessionLocal() as db:
        user = _user(request, db)
        error, rows = None, []
        try:
            rows = _load(db, user)
        except cnm.CnMaestroError as exc:
            error = str(exc)
        filtered = _filter(rows, q, group, state if state in STATES else "new")
        offset = max(0, int(offset or 0))
        return {
            "error": error, "total": len(filtered), "offset": offset, "limit": PAGE_SIZE, "max_batch": MAX_BATCH,
            "counts": {key: sum(1 for r in rows if r["state"] == key) for key in STATES},
            "uisp_sites": sorted({r["group"] for r in rows if r.get("group")}),
            "customers": [{"id": str(c.id), "name": c.name} for c in db.scalars(select(Customer).order_by(Customer.name))],
            "sites": [{"id": str(s.id), "name": s.name, "customer_id": str(s.customer_id)} for s in db.scalars(select(Site).order_by(Site.name))],
            "items": [_row(row) for row in filtered[offset:offset + PAGE_SIZE]],
        }


@router.post("/integrations/cnmaestro/onboarding/preview", dependencies=[Depends(require_csrf)])
def onboarding_preview(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = _user(request, db)
        try:
            customer, site, plan = preview_plan(db, user, _selection(body), _text(body, "customer_id"), _text(body, "site_id"))
        except (ValueError, cnm.CnMaestroError) as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        return {"customer": {"id": str(customer.id), "name": customer.name}, "site": {"id": str(site.id), "name": site.name} if site else None,
                "plan": [_row(row) for row in plan]}


@router.post("/integrations/cnmaestro/onboarding/commit", dependencies=[Depends(require_csrf)])
def onboarding_commit(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = _user(request, db)
        try:
            result = commit_selection(db, user, _selection(body), _text(body, "customer_id"), _text(body, "site_id"))
        except (ValueError, cnm.CnMaestroError) as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        db.commit()
        return {"customer_id": str(result["customer_id"]), "created": result["created"], "linked": result["linked"], "skipped": result["skipped"]}


def install_cnmaestro_onboarding(app) -> None:
    app.include_router(router)
