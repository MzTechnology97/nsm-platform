"""Core 0.24 extension for creating API keys with allow-listed read scopes."""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import main as core
from app.api_key_models import PlatformApiKey
from app.api_key_scopes import normalize_scopes
from app.api_keys import ALLOWED_EXPIRY_DAYS, _digest, _new_token, _render_admin
from app.db import SessionLocal
from app.models import Customer, utcnow
from app.security import validate_csrf

router = APIRouter()


def _remove_post_route(app, path: str):
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and "POST" in (getattr(route, "methods", set()) or set())
        )
    ]


def _canonicalize_scoped_post_route(app):
    """Keep only the newest POST /admin/api-keys route and move it first.

    ``include_router`` copies APIRoute objects, therefore endpoint identity is not
    a reliable selector across FastAPI versions. The Core 0.24 route has just
    been appended when this function runs, so the last exact POST match is the
    newly registered scoped handler. Any stale equivalent wrappers are dropped.
    """
    matches = []
    rest = []
    for route in app.router.routes:
        methods = getattr(route, "methods", set()) or set()
        if getattr(route, "path", None) == "/admin/api-keys" and "POST" in methods:
            matches.append(route)
        else:
            rest.append(route)
    if not matches:
        raise RuntimeError("Core 0.24 scoped API key POST route was not registered")
    canonical = matches[-1]
    app.router.routes[:] = [canonical] + rest


@router.post("/admin/api-keys", response_class=HTMLResponse, name="admin_api_key_create")
def admin_api_key_create_scoped(
    request: Request,
    name: str = Form(...),
    csrf: str = Form(...),
    customer_id: str = Form(""),
    expires_days: int = Form(90),
    scopes: list[str] = Form(default=[]),
):
    validate_csrf(request, csrf)
    name = str(name or "").strip()[:160]
    if not name:
        raise HTTPException(400, "Nome API key obbligatorio.")
    if expires_days not in ALLOWED_EXPIRY_DAYS:
        raise HTTPException(400, "Scadenza API key non valida.")
    selected_scopes = normalize_scopes(scopes)

    with SessionLocal() as db:
        user = core.require_admin(request, db)
        customer_uuid = None
        if customer_id.strip():
            try:
                customer_uuid = uuid.UUID(customer_id.strip())
            except ValueError as exc:
                raise HTTPException(400, "Cliente non valido.") from exc
            customer = db.get(Customer, customer_uuid)
            if not customer or not customer.is_active:
                raise HTTPException(400, "Cliente non valido o disattivato.")

        raw_key = _new_token()
        row = PlatformApiKey(
            name=name,
            key_prefix=raw_key[:20],
            key_hash=_digest(raw_key),
            scopes=selected_scopes,
            customer_id=customer_uuid,
            created_by_user_id=user.id,
            expires_at=utcnow() + timedelta(days=expires_days) if expires_days else None,
            is_active=True,
        )
        db.add(row)
        db.flush()
        core.add_event(
            db,
            "API_KEY_CREATED",
            actor=user,
            customer_id=customer_uuid,
            details={
                "api_key_id": str(row.id),
                "name": row.name,
                "prefix": row.key_prefix,
                "scopes": row.scopes,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            },
            source="portal",
        )
        db.commit()
        return _render_admin(request, db, user, new_key=raw_key, created_key=row)


def install_api_key_scope_extension(app):
    _remove_post_route(app, "/admin/api-keys")
    app.include_router(router)
    _canonicalize_scoped_post_route(app)
