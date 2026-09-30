"""Contextual feedback for browser-only Device CRUD actions.

The underlying CRUD handlers remain the source of truth for validation, audit
and persistence.  These wrappers only adapt expected HTTP validation/domain
errors into one-shot GUI feedback so operator actions do not escape into raw
FastAPI JSON pages.
"""
from __future__ import annotations

import uuid

from fastapi import Form, HTTPException, Request

from app import crud_extension as crud
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


def _device_error_feedback(request: Request, device_id: uuid.UUID, exc: HTTPException):
    if exc.status_code == 404:
        return flash_redirect(
            request,
            "/devices",
            "error",
            exception_message(exc, "Apparato non trovato."),
            title="Apparato non trovato",
        )
    if exc.status_code == 403:
        return flash_redirect(
            request,
            f"/devices/{device_id}",
            "error",
            exception_message(exc, "Non hai i permessi necessari per modificare l'apparato."),
            title="Operazione non autorizzata",
        )
    if exc.status_code in {400, 409}:
        return flash_redirect(
            request,
            f"/devices/{device_id}/manage",
            "warning",
            exception_message(exc, "Controlla i dati inseriti e riprova."),
            title="Dati apparato non validi",
        )
    raise exc


def install_ui_device_crud_feedback(app) -> None:
    for path in (
        "/devices/{device_id}/manage",
        "/devices/{device_id}/delete",
    ):
        _remove_post(app, path)

    @app.post("/devices/{device_id}/manage", name="device_manage")
    def device_manage_ui(
        request: Request,
        device_id: uuid.UUID,
        display_name: str = Form(""),
        customer_id: str = Form(...),
        site_id: str = Form(""),
        csrf: str = Form(...),
    ):
        try:
            response = crud.device_manage(
                request=request,
                device_id=device_id,
                display_name=display_name,
                customer_id=customer_id,
                site_id=site_id,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _device_error_feedback(request, device_id, exc)
        add_flash(
            request,
            "success",
            "Assegnazione e dati dell'apparato aggiornati.",
            title="Apparato aggiornato",
        )
        return response

    @app.post("/devices/{device_id}/delete", name="device_delete")
    def device_delete_ui(
        request: Request,
        device_id: uuid.UUID,
        confirm: str = Form(...),
        csrf: str = Form(...),
    ):
        try:
            response = crud.device_delete(
                request=request,
                device_id=device_id,
                confirm=confirm,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _device_error_feedback(request, device_id, exc)
        add_flash(
            request,
            "success",
            "Apparato eliminato insieme ai riferimenti di backup previsti dalla procedura.",
            title="Apparato eliminato",
        )
        return response
