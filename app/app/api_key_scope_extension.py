"""Core 0.24 extension for creating API keys with allow-listed read scopes."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app import main as core
from app.api_keys import _render_admin, create_key
from app.db import SessionLocal
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
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        row, raw_key = create_key(db, user, name, customer_id, expires_days, scopes)
        db.commit()
        return _render_admin(request, db, user, new_key=raw_key, created_key=row)


def install_api_key_scope_extension(app):
    _remove_post_route(app, "/admin/api-keys")
    app.include_router(router)
    _canonicalize_scoped_post_route(app)
