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


# Portal pages reachable from the search box: (label, url, keywords, permission or None).
PAGES = (
    ("Dashboard", "/", "home panoramica", None),
    ("Apparati", "/devices", "inventario dispositivi device", "devices.read"),
    ("Clienti", "/customers", "customer anagrafica", None),
    ("Backup", "/operations/backups", "configurazioni archivio", None),
    ("Firmware", "/operations/firmware", "aggiornamenti routeros update", "firmware.read"),
    ("Cataloghi firmware", "/operations/firmware/catalogs", "firmware ubiquiti tp-link tenda cambium huawei release versioni", "firmware.read"),
    ("Suggerimenti RouterOS", "/operations/firmware/suggestions", "aggiornamento piano firmware", "firmware.read"),
    ("Monitoraggio", "/operations/monitoring", "cpu memoria telemetria", None),
    ("Agent MikroTik", "/operations/agents", "fleet heartbeat", None),
    ("Vulnerabilità", "/security/vulnerabilities", "cve sicurezza advisory nvd", None),
    ("Ciclo di vita EOL/EOS", "/security/lifecycle", "eol eos fine supporto", None),
    ("Servizi esposti su Internet", "/security/exposure", "esposizione porte wan telnet ssh winbox scansione", "security.read"),
    ("Accessi ai dispositivi", "/security/access", "accessi login password brute force syslog tentativi", "security.read"),
    ("Syslog", "/admin/syslog", "syslog log server remoto", "users.manage"),
    ("Zabbix", "/admin/integrations/zabbix", "zabbix monitoraggio host", "users.manage"),
    ("Action Center", "/action-center", "problemi issue azioni", None),
    ("Report", "/audit/reports", "nis2 pdf csv", None),
    ("Eventi di audit", "/audit/events", "log storico", None),
    ("Integrazioni", "/integrations", "uisp genieacs nvd connettori", None),
    ("Profilo", "/profile", "password notifiche 2fa verifica due passaggi", None),
    ("Notifiche", "/admin/notifications", "smtp email telegram slack", "users.manage"),
    ("Utenti", "/admin/users", "account ruoli", "users.manage"),
    ("Sistema", "/admin/system", "worker backup database ripristino", "users.manage"),
)
_CVE_RE = re.compile(r"^cve-\d{4}-\d{2,}", re.I)


def _page_results(user, term: str) -> list[dict]:
    needle = term.lower()
    out = []
    for label, url, keywords, permission in PAGES:
        if permission and not core.has_permission(user, permission):
            continue
        if needle in label.lower() or any(word.startswith(needle) for word in keywords.split()):
            out.append({"type": "Pagina", "title": label, "subtitle": url, "url": url, "completion": label})
    return out[:4]


def _advisory_results(db, term: str) -> list[dict]:
    if not _CVE_RE.match(term) and "cve" not in term.lower():
        return []
    from app.models import DeviceVulnerability, SecurityAdvisory

    rows = db.execute(select(SecurityAdvisory, func.count(DeviceVulnerability.id))
                      .outerjoin(DeviceVulnerability, (DeviceVulnerability.advisory_id == SecurityAdvisory.id) & (DeviceVulnerability.status != "resolved"))
                      .where(SecurityAdvisory.cve_id.ilike(f"%{term}%")).group_by(SecurityAdvisory.id)
                      .order_by(SecurityAdvisory.cve_id.desc()).limit(5)).all()
    return [{"type": "Vulnerabilità", "title": advisory.cve_id, "subtitle": f"{advisory.vendor or ''} {advisory.product or ''} · {exposed} apparati esposti".strip(),
             "url": f"/security/vulnerabilities/{advisory.id}", "severity": (advisory.severity or "unknown").lower(), "completion": advisory.cve_id}
            for advisory, exposed in rows]


def _device_result(device, query: str):
    from app import vendor_cpe

    rank, match_field, match_value = _device_match(device, query)
    title = device.display_name or device.device_identity or device.name
    location = device.site.name if device.site else "senza sede"
    completion = match_value or title
    brand_key = vendor_cpe.brand(device)
    brand_label = vendor_cpe.label(brand_key) if brand_key else ("Altro produttore" if device.vendor == "generic" else (device.vendor or ""))
    return rank, {
        "type": "Apparato",
        "title": title,
        "subtitle": f"{device.customer.name} · {location} · {brand_label} · {device.model or 'modello non rilevato'}",
        "status": device.status or "unknown",
        "brand": brand_label,
        "icon": vendor_cpe.icon(device),
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

        total_devices = len(device_candidates)
        results = _page_results(user, term) + _advisory_results(db, term) + [item for _, item in ranked_devices]
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
        return {"results": results[:24], "query": term, "counts": {"Apparato": total_devices, "Cliente": len(customers), "Sede": len(sites)}}


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
