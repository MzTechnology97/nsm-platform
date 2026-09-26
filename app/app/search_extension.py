import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.db import SessionLocal
from app.models import Customer, Device, Site

router = APIRouter()


def _remove_route(app, path, method):
    method = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def _contains(value, term):
    return bool(value and term.casefold() in str(value).casefold())


def _device_match(device, term, compact_mac):
    candidates = [
        ("IP", device.management_ip),
        ("MAC", device.primary_mac),
        ("Seriale", device.serial_number),
        ("Alias", device.display_name),
        ("Identity", device.device_identity),
        ("Modello", device.model),
    ]
    for label, value in candidates:
        if _contains(value, term):
            return label
    if compact_mac and device.primary_mac:
        db_mac = re.sub(r"[^0-9A-Fa-f]", "", device.primary_mac).upper()
        if compact_mac in db_mac:
            return "MAC"
    return "Apparato"


@router.get("/api/v1/search/suggest", name="search_suggest_v2")
def search_suggest_v2(request: Request, q: str = ""):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return JSONResponse({"results": []}, status_code=401)

        term = q.strip()
        if len(term) < 2:
            return {"results": [], "query": term}

        like = f"%{term}%"
        normalized_mac = core.search_mac(term)
        compact_mac = re.sub(r"[^0-9A-Fa-f]", "", term).upper()

        customers = list(
            db.scalars(
                select(Customer)
                .where(or_(Customer.name.ilike(like), Customer.code.ilike(like)))
                .order_by(Customer.name)
                .limit(6)
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
        if len(compact_mac) >= 2:
            normalized_db_mac = func.replace(func.replace(Device.primary_mac, ":", ""), "-", "")
            device_filters.append(normalized_db_mac.ilike(f"%{compact_mac}%"))

        devices = list(
            db.scalars(
                select(Device)
                .options(selectinload(Device.customer), selectinload(Device.site))
                .where(or_(*device_filters))
                .order_by(Device.display_name.nullslast(), Device.device_identity.nullslast(), Device.name)
                .limit(10)
            )
        )

        sites = list(
            db.scalars(
                select(Site)
                .options(selectinload(Site.customer))
                .where(or_(Site.name.ilike(like), Site.address.ilike(like)))
                .order_by(Site.name)
                .limit(6)
            )
        )

        results = []
        for item in devices:
            location = item.site.name if item.site else "senza sede"
            identifier = item.management_ip or item.primary_mac or item.serial_number or "identificazione in attesa"
            results.append(
                {
                    "type": "Apparato",
                    "match": _device_match(item, term, compact_mac),
                    "title": item.display_name or item.device_identity or item.name,
                    "subtitle": f"{item.customer.name} · {location} · {item.vendor} · {identifier}",
                    "url": f"/devices/{item.id}",
                }
            )
        for item in customers:
            match = "Codice" if _contains(item.code, term) and not _contains(item.name, term) else "Nome"
            results.append(
                {
                    "type": "Cliente",
                    "match": match,
                    "title": item.name,
                    "subtitle": item.code or "Cliente",
                    "url": f"/customers/{item.id}",
                }
            )
        for item in sites:
            match = "Indirizzo" if _contains(item.address, term) and not _contains(item.name, term) else "Nome"
            results.append(
                {
                    "type": "Sede",
                    "match": match,
                    "title": item.name,
                    "subtitle": f"{item.customer.name} · {item.address or 'nessun indirizzo'}",
                    "url": f"/customers/{item.customer_id}#sites",
                }
            )

        return {"results": results[:18], "query": term}


def install_search_extension(app):
    _remove_route(app, "/api/v1/search/suggest", "GET")
    app.include_router(router)
