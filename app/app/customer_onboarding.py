import uuid

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import main as core
from app.db import SessionLocal
from app.models import Customer, Site
from app.security import validate_csrf


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


def _opt(value):
    text = str(value or "").strip()
    return text or None


def customer_new(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "customers.write"):
            raise HTTPException(403)
        return core.render(request, db, user, "customer_new.html")


async def customer_create(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))

    name = str(form.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "Nome cliente richiesto.")
    if len(name) > 180:
        raise HTTPException(400, "Nome cliente troppo lungo.")

    code = _opt(form.get("code"))
    notes = _opt(form.get("notes"))
    create_site = str(form.get("create_site", "")).lower() in {"1", "true", "on", "yes"}
    site_name = str(form.get("site_name", "")).strip()
    site_address = _opt(form.get("site_address"))
    site_notes = _opt(form.get("site_notes"))
    next_step = str(form.get("next_step", "profile")).strip().lower()

    if create_site and not site_name:
        raise HTTPException(
            400,
            "Inserisci il nome della prima sede oppure disattiva la creazione sede.",
        )
    if len(site_name) > 180:
        raise HTTPException(400, "Nome sede troppo lungo.")
    if next_step not in {"profile", "device"}:
        next_step = "profile"

    with SessionLocal() as db:
        user = core.require_permission(request, db, "customers.write")
        customer = Customer(name=name, code=code, notes=notes)
        db.add(customer)
        try:
            db.flush()
            core.add_event(
                db,
                "CUSTOMER_ADDED",
                actor=user,
                customer_id=customer.id,
                details={
                    "name": customer.name,
                    "code": customer.code,
                    "source": "customer_onboarding",
                },
            )

            site = None
            if create_site:
                site = Site(
                    customer_id=customer.id,
                    name=site_name,
                    address=site_address,
                    notes=site_notes,
                )
                db.add(site)
                db.flush()
                core.add_event(
                    db,
                    "SITE_ADDED",
                    actor=user,
                    customer_id=customer.id,
                    details={
                        "site_id": str(site.id),
                        "name": site.name,
                        "source": "customer_onboarding",
                    },
                )
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Codice cliente già utilizzato.")

        customer_id = customer.id
        site_id = site.id if site else None

    if next_step == "device":
        suffix = f"?site_id={site_id}" if site_id else ""
        return RedirectResponse(
            f"/customers/{customer_id}/devices/new{suffix}",
            status_code=303,
        )
    return RedirectResponse(f"/customers/{customer_id}", status_code=303)


def device_new(request: Request, customer_id: uuid.UUID, site_id: str = ""):
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

        selected_site_id = None
        if site_id:
            try:
                selected_uuid = uuid.UUID(site_id)
            except ValueError:
                raise HTTPException(400, "Sede non valida.")
            selected_site = next(
                (site for site in customer.sites if site.id == selected_uuid),
                None,
            )
            if not selected_site:
                raise HTTPException(400, "La sede non appartiene a questo cliente.")
            selected_site_id = selected_site.id

        site_rows = [
            {
                "site": site,
                "selected": bool(selected_site_id and site.id == selected_site_id),
            }
            for site in customer.sites
        ]

        return core.render(
            request,
            db,
            user,
            "device_new.html",
            customer=customer,
            site_rows=site_rows,
        )


def install_customer_onboarding(app):
    _remove_exact_route(app, "/customers/new/form", "GET")
    _remove_exact_route(app, "/customers/new/create", "POST")
    _remove_exact_route(app, "/customers/{customer_id}/devices/new", "GET")
    app.add_api_route(
        "/customers/new/form",
        customer_new,
        methods=["GET"],
        response_class=HTMLResponse,
        name="customer_new",
        include_in_schema=False,
    )
    app.add_api_route(
        "/customers/new/create",
        customer_create,
        methods=["POST"],
        name="customer_create",
        include_in_schema=False,
    )
    app.add_api_route(
        "/customers/{customer_id}/devices/new",
        device_new,
        methods=["GET"],
        response_class=HTMLResponse,
        name="device_new",
        include_in_schema=False,
    )
