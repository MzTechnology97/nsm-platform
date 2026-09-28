"""Core 0.39 — scalable RouterBOOT fleet readiness worklist.

This module is intentionally read-mostly: it classifies observed RouterBOOT
state and links to the explicit per-device lifecycle.  It does not provide bulk
flash/reboot actions.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select

from app import main as core
from app.db import SessionLocal
from app.models import Customer, Device, Site, utcnow

router = APIRouter()
PER_PAGE = 50
READINESS_STALE_AFTER = timedelta(hours=24)
ACTIVE_STATES = {"staging", "staged", "reboot_pending", "rebooting"}


def _as_utc(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _row(device: Device, now=None):
    now = now or utcnow()
    data = dict(device.inventory_data or {})
    readiness = dict(data.get("firmware_readiness") or {})
    lifecycle = dict(data.get("routerboot_lifecycle") or {})
    current = str(readiness.get("routerboard_current") or device.routerboot_version or "").strip()
    available = str(readiness.get("routerboard_upgrade") or "").strip()
    workflow = str(lifecycle.get("status") or "idle").strip().lower()
    transport = str(data.get("agent_transport") or "").strip().lower()
    profile = str(data.get("agent_privilege_profile") or "").strip()
    checked = _as_utc(readiness.get("checked_at"))
    stale = checked is None or now - checked > READINESS_STALE_AFTER

    reason = None
    if workflow in ACTIVE_STATES:
        state = "in_progress"
    elif workflow == "failed":
        state = "failed"
    elif workflow == "success" and lifecycle.get("observed_version") == lifecycle.get("target_version"):
        state = "current"
    elif current and available and current == available:
        state = "current"
    elif current and available:
        if transport != "modern":
            state, reason = "blocked", "agent legacy: RouterBOOT remoto richiede transport modern"
        elif profile != "ops-v1":
            state, reason = "blocked", "profilo agent non idoneo: reinstalla agent"
        elif device.status != "online":
            state, reason = "blocked", "apparato offline"
        else:
            state = "pending"
    else:
        state = "unknown"
        reason = "readiness RouterBOOT non disponibile"

    return {
        "device": device,
        "current": current or None,
        "available": available or None,
        "workflow": workflow,
        "state": state,
        "reason": reason,
        "checked_at": checked,
        "stale": stale,
        "transport": transport or None,
        "profile": profile or None,
        "target": lifecycle.get("target_version"),
        "last_error": lifecycle.get("last_error"),
    }


def _matches_state(row, state):
    if state == "all":
        return True
    if state == "attention":
        return row["state"] != "current"
    return row["state"] == state


@router.get("/operations/routerboot", response_class=HTMLResponse, name="routerboot_fleet")
def routerboot_fleet(
    request: Request,
    q: str = Query(""),
    customer: str = Query(""),
    site: str = Query(""),
    state: str = Query("attention"),
    page: int = Query(1, ge=1),
):
    allowed_states = {"attention", "pending", "in_progress", "blocked", "failed", "unknown", "current", "all"}
    if state not in allowed_states:
        state = "attention"
    customer_id = None
    site_id = None
    try:
        customer_id = uuid.UUID(customer) if customer else None
        site_id = uuid.UUID(site) if site else None
    except ValueError:
        raise HTTPException(400, "Filtro cliente/sede non valido.")

    with SessionLocal() as db:
        user = core.require_permission(request, db, "firmware.read")
        stmt = select(Device).where(Device.vendor == "mikrotik")
        if customer_id:
            stmt = stmt.where(Device.customer_id == customer_id)
        if site_id:
            stmt = stmt.where(Device.site_id == site_id)
        term = q.strip()
        if term:
            like = f"%{term}%"
            stmt = (
                stmt.join(Customer, Customer.id == Device.customer_id)
                .outerjoin(Site, Site.id == Device.site_id)
                .where(
                    or_(
                        Device.display_name.ilike(like),
                        Device.device_identity.ilike(like),
                        Device.name.ilike(like),
                        Device.model.ilike(like),
                        Device.serial_number.ilike(like),
                        Device.primary_mac.ilike(like),
                        Device.management_ip.ilike(like),
                        Customer.name.ilike(like),
                        Site.name.ilike(like),
                    )
                )
            )
        devices = db.scalars(stmt.order_by(Device.customer_id, Device.display_name, Device.device_identity, Device.name)).unique().all()
        rows_all = [_row(device) for device in devices]
        counters = {key: sum(1 for row in rows_all if row["state"] == key) for key in ("pending", "in_progress", "blocked", "failed", "unknown", "current")}
        counters["attention"] = len(rows_all) - counters["current"]
        counters["stale"] = sum(1 for row in rows_all if row["stale"])
        rows = [row for row in rows_all if _matches_state(row, state)]
        total = len(rows)
        pages = max(1, math.ceil(total / PER_PAGE))
        page = min(page, pages)
        start = (page - 1) * PER_PAGE
        rows = rows[start : start + PER_PAGE]

        customers = db.scalars(select(Customer).order_by(Customer.name)).all()
        site_stmt = select(Site).order_by(Site.name)
        if customer_id:
            site_stmt = site_stmt.where(Site.customer_id == customer_id)
        sites = db.scalars(site_stmt).all()
        selected_customer = db.get(Customer, customer_id) if customer_id else None
        selected_site = db.get(Site, site_id) if site_id else None
        if selected_site and customer_id and selected_site.customer_id != customer_id:
            raise HTTPException(400, "La sede non appartiene al cliente selezionato.")

        return core.templates.TemplateResponse(
            request=request,
            name="routerboot_fleet.html",
            context={
                "user": user,
                "rows": rows,
                "counters": counters,
                "total": total,
                "page": page,
                "pages": pages,
                "per_page": PER_PAGE,
                "q": term,
                "state_filter": state,
                "customer_filter": customer,
                "site_filter": site,
                "customers": customers,
                "sites": sites,
                "selected_customer": selected_customer,
                "selected_site": selected_site,
            },
        )


def install_routerboot_fleet(app):
    app.include_router(router)
