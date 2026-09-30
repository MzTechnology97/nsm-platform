"""Contextual feedback for browser-only API key administration actions.

The API-key domain handlers remain the source of truth for token generation,
validation, persistence and audit events.  This adapter only converts expected
browser validation/stale-state failures into one-shot GUI feedback.  Public
``/api/v1/public/*`` endpoints keep their existing JSON/HTTP contracts.
"""
from __future__ import annotations

import uuid

from fastapi import Form, HTTPException, Request

from app import api_keys
from app.ui_feedback import add_flash, exception_message, flash_redirect


def _remove_post(app, path: str) -> None:
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and "POST" in (getattr(route, "methods", set()) or set())
        )
    ]


def _promote_named_routes(app, names: tuple[str, ...]) -> None:
    selected = []
    remaining = []
    for route in app.router.routes:
        if getattr(route, "name", None) in names:
            selected.append(route)
        else:
            remaining.append(route)
    ordered = []
    for name in names:
        ordered.extend(route for route in selected if getattr(route, "name", None) == name)
    app.router.routes[:] = ordered + remaining


def _feedback(request: Request, exc: HTTPException):
    if exc.status_code == 403:
        return flash_redirect(
            request,
            "/",
            "error",
            exception_message(exc, "Non hai i permessi necessari per gestire le API key."),
            title="Operazione non autorizzata",
        )
    if exc.status_code == 404:
        return flash_redirect(
            request,
            "/admin/api-keys",
            "warning",
            exception_message(exc, "API key non trovata o già rimossa."),
            title="API key non disponibile",
        )
    if exc.status_code in {400, 409, 422}:
        return flash_redirect(
            request,
            "/admin/api-keys",
            "warning",
            exception_message(exc, "Controlla i dati inseriti e riprova."),
            title="Dati API key non validi",
        )
    raise exc


def install_ui_api_key_feedback(app) -> None:
    for path in (
        "/admin/api-keys",
        "/admin/api-keys/{key_id}/revoke",
        "/admin/api-keys/{key_id}/delete",
    ):
        _remove_post(app, path)

    @app.post("/admin/api-keys", name="admin_api_key_create_ui_feedback")
    def create_api_key_ui(
        request: Request,
        name: str = Form(""),
        customer_id: str = Form(""),
        expires_days: int = Form(90),
        csrf: str = Form(""),
        scopes: list[str] = Form(default=[]),
    ):
        try:
            response = api_keys.admin_api_key_create(
                request=request,
                name=name,
                customer_id=customer_id,
                expires_days=expires_days,
                csrf=csrf,
                scopes=scopes,
            )
        except HTTPException as exc:
            return _feedback(request, exc)
        # Creation intentionally remains an inline HTML response because the
        # raw token is shown exactly once on this response.
        add_flash(
            request,
            "success",
            "API key creata. Copia ora il token: non verrà mostrato di nuovo.",
            title="API key creata",
        )
        return response

    @app.post(
        "/admin/api-keys/{key_id}/revoke",
        name="admin_api_key_revoke_ui_feedback",
    )
    def revoke_api_key_ui(
        request: Request,
        key_id: uuid.UUID,
        csrf: str = Form(""),
    ):
        try:
            response = api_keys.admin_api_key_revoke(
                request=request,
                key_id=key_id,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _feedback(request, exc)
        add_flash(
            request,
            "success",
            "API key revocata. Le richieste che la utilizzano non saranno più autorizzate.",
            title="API key revocata",
        )
        return response

    @app.post(
        "/admin/api-keys/{key_id}/delete",
        name="admin_api_key_delete_ui_feedback",
    )
    def delete_api_key_ui(
        request: Request,
        key_id: uuid.UUID,
        csrf: str = Form(""),
    ):
        try:
            response = api_keys.admin_api_key_delete(
                request=request,
                key_id=key_id,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _feedback(request, exc)
        add_flash(
            request,
            "success",
            "API key revocata eliminata definitivamente dall'archivio.",
            title="API key eliminata",
        )
        return response

    _promote_named_routes(
        app,
        (
            "admin_api_key_create_ui_feedback",
            "admin_api_key_revoke_ui_feedback",
            "admin_api_key_delete_ui_feedback",
        ),
    )
