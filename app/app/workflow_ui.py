import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.config import settings
from app.db import SessionLocal
from app.models import Customer, Device, Site
from app.preferences import PlatformBranding
from app.security import validate_csrf

router = APIRouter()

DEFAULT_COLORS = {
    "primary": ("primary_color", "#1f5f8b"),
    "sidebar": ("sidebar_color", "#111827"),
    "highlight": ("highlight_color", "#f59e0b"),
}


def _branding(db):
    item = db.get(PlatformBranding, 1)
    if item:
        return item
    item = PlatformBranding(
        id=1,
        portal_name=settings.app_name,
        tagline="Network operations & security management",
        primary_color=DEFAULT_COLORS["primary"][1],
        sidebar_color=DEFAULT_COLORS["sidebar"][1],
        highlight_color=DEFAULT_COLORS["highlight"][1],
    )
    db.add(item)
    db.flush()
    return item


def backup_schedule_label(value: str | None) -> str:
    cron = (value or "").strip()
    parts = cron.split()
    if len(parts) != 5:
        return cron or "Non definita"
    minute, hour, day, month, weekday = parts
    if minute == "0" and hour.startswith("*/") and day == "*" and month == "*" and weekday == "*":
        return f"Ogni {hour[2:]} ore"
    hh = hour.zfill(2) if hour.isdigit() else hour
    mm = minute.zfill(2) if minute.isdigit() else minute
    if day == "*" and month == "*" and weekday == "*":
        return f"Ogni giorno alle {hh}:{mm}"
    if day == "*" and month == "*" and weekday != "*":
        days = {"0": "domenica", "1": "lunedì", "2": "martedì", "3": "mercoledì", "4": "giovedì", "5": "venerdì", "6": "sabato", "7": "domenica"}
        return f"Ogni {days.get(weekday, weekday)} alle {hh}:{mm}"
    if day != "*" and month == "*" and weekday == "*":
        return f"Ogni mese, giorno {day}, alle {hh}:{mm}"
    return cron


@router.get("/customers/new/form", response_class=HTMLResponse)
def customer_new(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "customers.write"):
            raise HTTPException(403)
        return core.render(request, db, user, "customer_new.html")


@router.get("/customers/{customer_id}/sites/new", response_class=HTMLResponse)
def site_new(request: Request, customer_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "customers.write"):
            raise HTTPException(403)
        customer = db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(404)
        return core.render(request, db, user, "site_new.html", customer=customer)


@router.get("/customers/{customer_id}/devices/new", response_class=HTMLResponse)
def device_new(request: Request, customer_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.write"):
            raise HTTPException(403)
        customer = db.scalar(
            select(Customer)
            .where(Customer.id == customer_id)
            .options(selectinload(Customer.sites))
        )
        if not customer:
            raise HTTPException(404)
        return core.render(request, db, user, "device_new.html", customer=customer)


@router.get("/operations/backups/policies/new", response_class=HTMLResponse)
def backup_policy_new(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "backup.configure"):
            raise HTTPException(403)
        customers = list(db.scalars(select(Customer).order_by(Customer.name)))
        devices = list(
            db.scalars(
                select(Device)
                .options(selectinload(Device.customer))
                .order_by(Device.display_name.nullslast(), Device.name)
            )
        )
        return core.render(
            request,
            db,
            user,
            "backup_policy_new.html",
            customers=customers,
            devices=devices,
        )


@router.get("/api/v1/search/suggest")
def search_suggest(request: Request, q: str = ""):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return JSONResponse({"results": []}, status_code=401)
        term = q.strip()
        if len(term) < 2:
            return {"results": []}
        like = f"%{term}%"
        normalized_mac = core.search_mac(term)

        customers = list(
            db.scalars(
                select(Customer)
                .where(or_(Customer.name.ilike(like), Customer.code.ilike(like)))
                .order_by(Customer.name)
                .limit(5)
            )
        )

        device_filters = [
            Device.display_name.ilike(like),
            Device.device_identity.ilike(like),
            Device.name.ilike(like),
            Device.model.ilike(like),
            Device.serial_number.ilike(like),
            Device.primary_mac.ilike(like),
            Device.management_ip.ilike(like),
        ]
        if normalized_mac:
            device_filters.append(Device.primary_mac == normalized_mac)
        devices = list(
            db.scalars(
                select(Device)
                .options(selectinload(Device.customer))
                .where(or_(*device_filters))
                .order_by(Device.display_name.nullslast(), Device.name)
                .limit(8)
            )
        )

        sites = list(
            db.scalars(
                select(Site)
                .options(selectinload(Site.customer))
                .where(or_(Site.name.ilike(like), Site.address.ilike(like)))
                .order_by(Site.name)
                .limit(5)
            )
        )

        results = []
        for item in customers:
            results.append({
                "type": "Cliente",
                "title": item.name,
                "subtitle": item.code or "Cliente",
                "url": f"/customers/{item.id}",
            })
        for item in devices:
            results.append({
                "type": "Apparato",
                "title": item.display_name or item.device_identity or item.name,
                "subtitle": f"{item.customer.name} · {item.vendor} · {item.primary_mac or item.serial_number or item.management_ip or 'identificazione in attesa'}",
                "url": f"/devices/{item.id}",
            })
        for item in sites:
            results.append({
                "type": "Sede",
                "title": item.name,
                "subtitle": f"{item.customer.name} · {item.address or 'nessun indirizzo'}",
                "url": f"/customers/{item.customer_id}#sites",
            })
        return {"results": results[:12], "query": term}


@router.post("/admin/branding/colors/{color_key}/reset")
async def reset_branding_color(request: Request, color_key: str):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    if color_key not in DEFAULT_COLORS:
        raise HTTPException(404)
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        branding = _branding(db)
        attr, default = DEFAULT_COLORS[color_key]
        old_value = getattr(branding, attr)
        setattr(branding, attr, default)
        core.add_event(
            db,
            "PLATFORM_BRANDING_COLOR_RESET",
            actor=user,
            details={"color": color_key, "old_value": old_value, "new_value": default},
        )
        db.commit()
    return RedirectResponse("/admin/branding?status=color_reset", status_code=303)


def install_workflow_ui(app, templates):
    templates.env.globals["backup_schedule_label"] = backup_schedule_label
    app.include_router(router)
