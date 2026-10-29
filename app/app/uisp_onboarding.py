"""Bulk onboarding of UISP devices into NSM (UBNT-03).

The operator picks UISP devices that are not in NSM yet, chooses the NSM
Customer (and optional Site) explicitly, reviews a preview and confirms.  This
manual flow never derives the Customer/Site from UISP metadata; the automatic
import of customer endpoints by name lives in ``uisp_auto_customer`` (opt-out,
new devices only, existing records keep their Customer).  At confirmation the UISP
list is fetched again, so a device removed or changed in the meantime is
reported instead of being created from stale data.  Devices already present
with the same MAC but not yet linked are associated, never duplicated.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select

from app import main as core
from app import uisp_connector as uisp
from app.models import Customer, Device, Site

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
    every = {"include_decommissioned": True}  # a decommissioned device is still in NSM: never import it twice
    linked = {d.external_device_id: d for d in db.scalars(select(Device).where(Device.vendor == "ubiquiti", Device.external_device_id.is_not(None))
                                                          .execution_options(**every))}
    by_mac = {}
    for device in db.scalars(select(Device).where(Device.primary_mac.is_not(None)).execution_options(**every)):
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
        elif c["external_id"] in linked and linked[c["external_id"]].decommissioned_at is not None:
            state, reason = "blocked", "Apparato disattivato in NSM: riattivalo per riprendere la sincronizzazione."
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


def target(db, customer_id, site_id):
    """Customer (required) and optional site of that customer chosen by the operator. ValueError with the reason."""
    try:
        customer = db.get(Customer, uuid.UUID(str(customer_id or "")))
    except ValueError:
        customer = None
    if not customer:
        raise ValueError("Seleziona il cliente NSM a cui assegnare i nuovi apparati.")
    site = None
    if site_id:
        try:
            site = db.get(Site, uuid.UUID(str(site_id)))
        except ValueError:
            site = None
        if not site or site.customer_id != customer.id:
            raise ValueError("La sede scelta non appartiene al cliente.")
    return customer, site


def preview_plan(db, user, selected: list[str], customer_id, site_id):
    """What would happen to each selected UISP device, from fresh UISP data.

    Returns (customer, site, plan). Raises ValueError or ``UispConnectorError``.
    """
    if not selected:
        raise ValueError("Seleziona almeno un dispositivo UISP.")
    if len(selected) > MAX_BATCH:
        raise ValueError(f"Massimo {MAX_BATCH} dispositivi per operazione.")
    customer, site = target(db, customer_id, site_id)
    _, rows = _load(db, user)
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
    return customer, site, plan


def commit_selection(db, user, selected: list[str], customer_id, site_id) -> dict:
    """Create or link the selected devices from fresh UISP data, audited.

    Returns {customer_id, created, linked, skipped}. Raises ValueError or ``UispConnectorError``. Caller commits.
    """
    if not selected or len(selected) > MAX_BATCH:
        raise ValueError(f"Selezione vuota o oltre {MAX_BATCH} dispositivi.")
    customer, site = target(db, customer_id, site_id)
    _, rows = _load(db, user)  # fresh UISP data, never the preview page
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
    core.add_event(db, "UISP_BULK_ONBOARDING", actor=user, customer_id=customer.id,
                   details={"created": created, "linked": linked, "skipped": len(skipped), "site_id": str(site.id) if site else None})
    return {"customer_id": customer.id, "created": created, "linked": linked, "skipped": skipped}


