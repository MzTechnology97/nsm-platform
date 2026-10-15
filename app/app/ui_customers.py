"""Customers for the new interface (phase 2).

- ``GET /api/ui/v1/customers``: same health counters as the classic list
  (devices, sites, firmware to update, devices with severe CVEs, open Action
  Center issues) plus online/offline devices.  The list is small (one row per
  customer), so filters on the counters and sorting happen after counting.
- ``GET /api/ui/v1/customers/{id}``: the customer page (header, overview cards
  with backup protection and lifecycle, open issues, recent events, sites with
  their device states), built with the classic overview helpers.
- ``GET /api/ui/v1/customers/{id}/graphs/summary``: what surrounds the charts
  of the Graphs tab (sites compared over 24 hours, devices with most traffic,
  30-day availability); the charts come from ``/api/v1/customers/{id}/graphs``.
- ``POST /api/ui/v1/customers`` creates a customer; ``POST .../{id}`` updates
  it, ``POST .../{id}/delete`` deletes it with devices, sites and backup files
  (the typed name must match); ``POST .../{id}/sites`` creates a site,
  ``POST .../sites/{site_id}`` updates it and ``.../delete`` removes it (its
  devices stay in the customer without a site). All need ``customers.write``
  and the CSRF header and go through the functions shared with the classic
  forms (``main.create_customer_record`` / ``create_site_record``,
  ``crud_extension.update_*`` / ``delete_*``). Validation errors are 422
  ``{code, message}``: ``name_required``, ``code_taken``, ``confirm_name``.

Customer scope comes from the global ORM filter (another customer is a 404).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func, or_, select

from datetime import timedelta

from app import availability, crud_extension, customer_graphs
from app import main as core
from app.customer_tabs import _backup_summary, _load_customer
from app.customer_workspace import OPEN_STATES, _customer_metric_maps, _customer_metrics
from app.db import SessionLocal
from app.models import ActionIssue, AuditEvent, Customer, Device, Site, utcnow
from app.ui_api import brand_icon, require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
MAX_ROWS = 5000
FILTERS = {
    "alerts": lambda row: row["alerts"] > 0,
    "severe": lambda row: row["severe"] > 0,
    "updates": lambda row: row["updates"] > 0,
    "offline": lambda row: row["offline"] > 0,
}
COUNTERS = ("devices", "offline", "updates", "severe", "alerts")


def _status_counts(db, ids) -> dict:
    counts: dict = {}
    if not ids:
        return counts
    for customer_id, status, total in db.execute(select(Device.customer_id, Device.status, func.count(Device.id))
                                                 .where(Device.customer_id.in_(ids)).group_by(Device.customer_id, Device.status)):
        counts.setdefault(customer_id, {})[status] = int(total)
    return counts


def customer_rows(db, q: str = "") -> list[dict]:
    term = (q or "").strip()[:100]
    stmt = select(Customer)
    if term:
        like = f"%{term}%"
        stmt = stmt.where(or_(Customer.name.ilike(like), Customer.code.ilike(like)))
    customers = list(db.scalars(stmt.order_by(Customer.name).limit(MAX_ROWS)))
    ids = [c.id for c in customers]
    maps = _customer_metric_maps(db, ids)
    statuses = _status_counts(db, ids)
    rows = []
    for customer in customers:
        metrics = _customer_metrics(maps, customer.id)
        by_status = statuses.get(customer.id, {})
        rows.append({
            "id": str(customer.id), "name": customer.name, "code": customer.code, "active": bool(customer.is_active),
            "sites": metrics["sites"], "devices": metrics["devices"],
            "online": by_status.get("online", 0), "offline": by_status.get("offline", 0),
            "updates": metrics["updates"], "severe": metrics["severe_cve_devices"],
            "alerts": metrics["alerts"], "backup_alerts": metrics["backup_alerts"],
        })
    return rows


@router.get("/customers")
def customers(request: Request, q: str = "", filter: str = "", sort: str = "name", dir: str = ""):
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.read")
        rows = customer_rows(db, q)
        counts = {"all": len(rows), **{key: sum(1 for row in rows if test(row)) for key, test in FILTERS.items()}}
        if filter in FILTERS:
            rows = [row for row in rows if FILTERS[filter](row)]
        sort = sort if sort in COUNTERS else "name"
        # Counters read best from the largest down unless asked otherwise; ties and names stay A→Z.
        descending = dir == "desc" if dir in ("asc", "desc") else sort != "name"
        if sort == "name":
            rows.sort(key=lambda row: row["name"].lower(), reverse=descending)
        else:
            rows.sort(key=lambda row: (-row[sort] if descending else row[sort], row["name"].lower()))
        return {
            "total": len(rows), "items": rows, "counts": counts,
            "can_create": core.has_permission(user, "customers.write"),
        }


CUSTOMER_TABS = (("overview", ""), ("devices", "/devices"), ("sites", "/sites"), ("graphs", "/graphs"),
                 ("backups", "/backups"), ("security", "/security"), ("history", "/history"))


def _iso(value):
    return value.isoformat() if value else None


@router.get("/customers/{customer_id}")
def customer_detail(request: Request, customer_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.read")
        try:
            customer = _load_customer(db, customer_id)
        except HTTPException as exc:
            raise HTTPException(404, "Cliente non trovato.") from exc
        metrics = _customer_metrics(_customer_metric_maps(db, [customer.id]), customer.id)
        devices = list(customer.devices)
        by_status = {}
        for device in devices:
            by_status[device.status or ""] = by_status.get(device.status or "", 0) + 1
        issues = list(db.scalars(select(ActionIssue).where(ActionIssue.customer_id == customer.id, ActionIssue.status.in_(OPEN_STATES))
                                 .order_by(ActionIssue.updated_at.desc()).limit(6)))
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.customer_id == customer.id).order_by(AuditEvent.timestamp.desc()).limit(8)))
        lifecycle = sum(1 for d in devices if str(d.lifecycle_status or "").lower() in ("eol", "eos"))
        unassigned = [d for d in devices if d.site_id is None]
        base = f"/customers/{customer.id}"
        return {
            "customer": {"id": str(customer.id), "name": customer.name, "code": customer.code, "active": bool(customer.is_active), "notes": customer.notes},
            "counts": {"devices": len(devices), "sites": len(customer.sites), "online": by_status.get("online", 0), "offline": by_status.get("offline", 0),
                       "pending": sum(n for status, n in by_status.items() if status.startswith("pending")), "unassigned": len(unassigned)},
            "metrics": {"severe": metrics["severe_cve_devices"], "updates": metrics["updates"], "alerts": metrics["alerts"],
                        "backup_alerts": metrics["backup_alerts"], "lifecycle": lifecycle, "backup": _backup_summary(db, customer)},
            "issues": [{"id": str(i.id), "title": i.title, "severity": i.severity, "category": i.category, "updated_at": _iso(i.updated_at),
                        "device_id": str(i.device_id) if i.device_id else None} for i in issues],
            "events": [{"timestamp": _iso(e.timestamp), "type": e.event_type, "label": core.event_label(e.event_type), "source": e.source,
                        "result": e.result, "severity": e.severity} for e in events],
            "sites": sorted(({"id": str(site.id), "name": site.name, "address": site.address, "notes": site.notes,
                              "devices": len(site.devices), "online": sum(1 for d in site.devices if d.status == "online"),
                              "offline": sum(1 for d in site.devices if d.status == "offline")} for site in customer.sites),
                            key=lambda row: row["name"].lower()),
            "actions": {"edit": core.has_permission(user, "customers.write"), "add_site": core.has_permission(user, "customers.write"),
                        "add_device": core.has_permission(user, "devices.write"), "incidents": core.has_permission(user, "incidents.read")},
            "tabs": [{"key": key, "classic": f"{base}{suffix}"} for key, suffix in CUSTOMER_TABS],
            "links": {"edit": f"{base}/edit", "add_site": f"{base}/sites/new", "add_device": f"{base}/devices/new",
                      "incidents": f"/incidents?customer={customer.id}&status=all", "action_center": f"/action-center?customer={customer.id}"},
        }


def _top_traffic(db, devices, now) -> list[dict]:
    """Devices with the most average WAN traffic over 24 hours (same figures as the classic Graphs page)."""
    window, width = customer_graphs.RANGES["24h"]
    per_device = customer_graphs.traffic(db, devices, now - window, width)["per_device"]
    by_id = {d.id: d for d in devices}
    rows = sorted(((by_id[i], v[0] / v[2], v[1] / v[2]) for i, v in per_device.items() if v[2]), key=lambda row: -(row[1] + row[2]))
    return [{"id": str(d.id), "name": d.display_name or d.device_identity or d.name, "brand": brand_icon(d), "rx_bps": round(rx, 1), "tx_bps": round(tx, 1)}
            for d, rx, tx in rows[:customer_graphs.TOP_DEVICES]]


@router.get("/customers/{customer_id}/graphs/summary")
def customer_graphs_summary(request: Request, customer_id: uuid.UUID, site: str = ""):
    with SessionLocal() as db:
        ui_user(request, db, "monitoring.read")
        customer = db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(404, "Cliente non trovato.")
        site_id = customer_graphs._site_id(db, customer, site)
        selected = next((s for s in customer.sites if s.id == site_id), None)
        now = utcnow()
        devices = customer_graphs.scope_devices(db, customer, site_id)
        scope = [d.id for d in devices]
        overview = customer_graphs.site_overview(db, customer, now) if not selected else []
        section = availability.report_section(db, scope, now - timedelta(days=30), now)
        return {
            "site": {"id": str(selected.id), "name": selected.name} if selected else None,
            "sites": [{"id": str(s.id), "name": s.name} for s in sorted(customer.sites, key=lambda s: s.name.lower())],
            "devices": len(scope),
            "overview": [{"site": {"id": str(row["site"].id), "name": row["site"].name} if row["site"] else None,
                          "devices": row["devices"], "online": row["online"], "rx_bps": row["rx_bps"], "tx_bps": row["tx_bps"], "peak_bps": row["peak_bps"]}
                         for row in overview],
            "top": _top_traffic(db, devices, now),
            "availability": {"measured": section["measured"], "average": section["average"], "below_target": section["below_target"],
                             "target": section["target"],
                             "devices": [{k: row.get(k) for k in ("device_id", "device", "availability", "downtime_minutes", "outages",
                                                                  "longest_outage_minutes", "loss", "rtt_avg")} for row in section["devices"][:50]]},
            "ranges": list(customer_graphs.RANGES),
        }


def install_ui_customers(app) -> None:
    app.include_router(router)


# --- Customer and site forms -----------------------------------------------------------------------

ERROR_CODES = {400: "name_required", 409: "code_taken"}


def _text(body: dict, key: str) -> str:
    value = body.get(key)
    return value if isinstance(value, str) else ""


def _form_error(exc: HTTPException) -> HTTPException:
    """Shared-function validation (400 / 409) as 422 ``{code, message}``; other statuses pass through."""
    code = ERROR_CODES.get(exc.status_code)
    if not code:
        return exc
    return HTTPException(422, {"code": code, "message": str(exc.detail)})


def _site(db, customer_id: uuid.UUID, site_id: uuid.UUID, with_devices: bool = False):
    from sqlalchemy.orm import selectinload

    stmt = select(Site).where(Site.id == site_id, Site.customer_id == customer_id)
    if with_devices:
        stmt = stmt.options(selectinload(Site.devices))
    site = db.scalar(stmt)
    if not site:
        raise HTTPException(404)
    return site


@router.post("/customers", dependencies=[Depends(require_csrf)])
def create_customer(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.write")
        try:
            customer = core.create_customer_record(db, user, _text(body, "name"), _text(body, "code"), _text(body, "notes"))
        except HTTPException as exc:
            raise _form_error(exc) from None
        return {"id": str(customer.id)}


@router.post("/customers/{customer_id}", dependencies=[Depends(require_csrf)])
def update_customer(request: Request, customer_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.write")
        customer = db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(404)
        try:
            crud_extension.update_customer_record(db, user, customer, _text(body, "name"), _text(body, "code"), _text(body, "notes"))
        except HTTPException as exc:
            raise _form_error(exc) from None
        return {"id": str(customer_id)}


@router.post("/customers/{customer_id}/delete", dependencies=[Depends(require_csrf)])
def delete_customer(request: Request, customer_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.write")
        customer = crud_extension._customer_with_inventory(db, customer_id)
        if not customer:
            raise HTTPException(404)
        try:
            crud_extension.delete_customer_record(db, user, customer, _text(body, "confirm_name"))
        except ValueError:
            raise HTTPException(422, {"code": "confirm_name", "message": "Il nome di conferma non corrisponde al cliente."}) from None
        return {"deleted": True}


@router.post("/customers/{customer_id}/sites", dependencies=[Depends(require_csrf)])
def create_site(request: Request, customer_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.write")
        try:
            site = core.create_site_record(db, user, customer_id, _text(body, "name"), _text(body, "address"), _text(body, "notes"))
        except HTTPException as exc:
            raise _form_error(exc) from None
        return {"id": str(site.id)}


@router.post("/customers/{customer_id}/sites/{site_id}", dependencies=[Depends(require_csrf)])
def update_site(request: Request, customer_id: uuid.UUID, site_id: uuid.UUID, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.write")
        site = _site(db, customer_id, site_id)
        try:
            crud_extension.update_site_record(db, user, site, _text(body, "name"), _text(body, "address"), _text(body, "notes"))
        except HTTPException as exc:
            raise _form_error(exc) from None
        return {"id": str(site_id)}


@router.post("/customers/{customer_id}/sites/{site_id}/delete", dependencies=[Depends(require_csrf)])
def delete_site(request: Request, customer_id: uuid.UUID, site_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "customers.write")
        site = _site(db, customer_id, site_id, with_devices=True)
        moved = crud_extension.delete_site_record(db, user, site)
        return {"moved": moved}
