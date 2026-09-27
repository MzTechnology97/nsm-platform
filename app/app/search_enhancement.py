import re

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.db import SessionLocal
from app.models import Customer, Device, Site


def _remove_exact_route(app, path: str, method: str):
    method = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def _promote_route(app, path: str, name: str):
    matches = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == path
        and getattr(route, "name", None) == name
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    if not matches:
        return
    route = matches[-1]
    app.router.routes[:] = [route] + [
        item for item in app.router.routes if id(item) != id(route)
    ]


def _compact(value: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", value or "").lower()


def _rank_text(value: str | None, query: str):
    if not value:
        return None
    value_l = value.lower()
    query_l = query.lower()
    if value_l == query_l:
        return 0
    if value_l.startswith(query_l):
        return 1
    if query_l in value_l:
        return 2
    return None


def _device_match(device, query: str):
    compact_query = _compact(query)
    candidates = []

    if device.primary_mac:
        compact_mac = _compact(device.primary_mac)
        if compact_query and compact_query == compact_mac:
            candidates.append((0, "MAC", device.primary_mac))
        elif compact_query and compact_mac.startswith(compact_query):
            candidates.append((1, "MAC", device.primary_mac))
        elif compact_query and compact_query in compact_mac:
            candidates.append((2, "MAC", device.primary_mac))

    fields = [
        ("Seriale", device.serial_number, 10),
        ("IP", device.management_ip, 20),
        ("Alias", device.display_name, 30),
        ("Identity", device.device_identity or device.name, 40),
        ("Modello", device.model, 50),
        ("ID esterno", device.external_device_id, 60),
    ]
    for label, value, weight in fields:
        rank = _rank_text(value, query)
        if rank is not None:
            candidates.append((weight + rank, label, value))

    if not candidates:
        return (999, "Apparato", device.display_name or device.device_identity or device.name or "")
    return min(candidates, key=lambda item: item[0])


def _device_result(device, query: str):
    rank, match_field, match_value = _device_match(device, query)
    title = device.display_name or device.device_identity or device.name
    location = device.site.name if device.site else "senza sede"
    completion = match_value or title
    return rank, {
        "type": "Apparato",
        "title": title,
        "subtitle": f"{device.customer.name} · {location} · {device.vendor} · {device.model or 'modello non rilevato'}",
        "url": f"/devices/{device.id}",
        "match_field": match_field,
        "match_value": match_value,
        "completion": completion,
        "serial": device.serial_number,
        "mac": device.primary_mac,
        "ip": device.management_ip,
        "model": device.model,
        "customer": device.customer.name,
        "site": device.site.name if device.site else None,
    }


def search_suggest_enhanced(request: Request, q: str = ""):
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

        device_filters = [
            Device.display_name.ilike(like),
            Device.device_identity.ilike(like),
            Device.name.ilike(like),
            Device.model.ilike(like),
            Device.serial_number.ilike(like),
            Device.primary_mac.ilike(like),
            Device.management_ip.ilike(like),
            Device.external_device_id.ilike(like),
        ]
        if normalized_mac:
            device_filters.append(Device.primary_mac == normalized_mac)
        if len(compact_mac) >= 2:
            normalized_db_mac = func.replace(func.replace(Device.primary_mac, ":", ""), "-", "")
            device_filters.append(normalized_db_mac.ilike(f"%{compact_mac}%"))

        device_candidates = list(
            db.scalars(
                select(Device)
                .options(selectinload(Device.customer), selectinload(Device.site))
                .where(or_(*device_filters))
                .limit(40)
            )
        )
        ranked_devices = sorted(
            (_device_result(device, term) for device in device_candidates),
            key=lambda item: (item[0], item[1]["title"].lower()),
        )[:10]

        customers = list(
            db.scalars(
                select(Customer)
                .where(or_(Customer.name.ilike(like), Customer.code.ilike(like)))
                .order_by(Customer.name)
                .limit(5)
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

        results = [item for _, item in ranked_devices]
        for item in customers:
            matched_code = bool(item.code and term.lower() in item.code.lower())
            results.append(
                {
                    "type": "Cliente",
                    "title": item.name,
                    "subtitle": item.code or "Cliente",
                    "url": f"/customers/{item.id}",
                    "match_field": "Codice" if matched_code else "Cliente",
                    "match_value": item.code if matched_code else item.name,
                    "completion": item.code if matched_code else item.name,
                }
            )
        for item in sites:
            matched_address = bool(item.address and term.lower() in item.address.lower())
            results.append(
                {
                    "type": "Sede",
                    "title": item.name,
                    "subtitle": f"{item.customer.name} · {item.address or 'nessun indirizzo'}",
                    "url": f"/customers/{item.customer_id}/sites",
                    "match_field": "Indirizzo" if matched_address else "Sede",
                    "match_value": item.address if matched_address else item.name,
                    "completion": item.address if matched_address else item.name,
                }
            )
        return {"results": results[:20], "query": term}


def install_search_enhancement(app):
    _remove_exact_route(app, "/api/v1/search/suggest", "GET")
    app.add_api_route(
        "/api/v1/search/suggest",
        search_suggest_enhanced,
        methods=["GET"],
        name="search_suggest",
        include_in_schema=False,
    )
    _promote_route(app, "/api/v1/search/suggest", "search_suggest")
