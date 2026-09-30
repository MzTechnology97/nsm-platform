"""Contextual feedback for browser-only Customer and Site CRUD actions."""
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


def _customer_error_feedback(request: Request, customer_id: uuid.UUID, exc: HTTPException):
    if exc.status_code == 404:
        return flash_redirect(
            request,
            "/customers",
            "error",
            exception_message(exc, "Cliente non trovato."),
            title="Cliente non trovato",
        )
    if exc.status_code == 403:
        return flash_redirect(
            request,
            f"/customers/{customer_id}",
            "error",
            exception_message(exc, "Non hai i permessi necessari per modificare il cliente."),
            title="Operazione non autorizzata",
        )
    if exc.status_code in {400, 409}:
        return flash_redirect(
            request,
            f"/customers/{customer_id}/edit",
            "warning",
            exception_message(exc, "Controlla i dati del cliente e riprova."),
            title="Dati cliente non validi",
        )
    raise exc


def _site_error_feedback(
    request: Request,
    customer_id: uuid.UUID,
    site_id: uuid.UUID,
    exc: HTTPException,
):
    if exc.status_code == 404:
        return flash_redirect(
            request,
            f"/customers/{customer_id}#sites",
            "warning",
            exception_message(exc, "Sede non trovata o non appartenente al cliente."),
            title="Sede non disponibile",
        )
    if exc.status_code == 403:
        return flash_redirect(
            request,
            f"/customers/{customer_id}",
            "error",
            exception_message(exc, "Non hai i permessi necessari per modificare la sede."),
            title="Operazione non autorizzata",
        )
    if exc.status_code in {400, 409}:
        return flash_redirect(
            request,
            f"/customers/{customer_id}/sites/{site_id}/edit",
            "warning",
            exception_message(exc, "Controlla i dati della sede e riprova."),
            title="Dati sede non validi",
        )
    raise exc


def install_ui_customer_site_crud_feedback(app) -> None:
    for path in (
        "/customers/{customer_id}/edit",
        "/customers/{customer_id}/delete",
        "/customers/{customer_id}/sites/{site_id}/edit",
        "/customers/{customer_id}/sites/{site_id}/delete",
    ):
        _remove_post(app, path)

    @app.post("/customers/{customer_id}/edit", name="customer_edit_ui_feedback")
    def customer_edit_ui(
        request: Request,
        customer_id: uuid.UUID,
        name: str = Form(...),
        code: str = Form(""),
        notes: str = Form(""),
        csrf: str = Form(...),
    ):
        try:
            response = crud.customer_edit(
                request=request,
                customer_id=customer_id,
                name=name,
                code=code,
                notes=notes,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _customer_error_feedback(request, customer_id, exc)
        add_flash(
            request,
            "success",
            "Dati del cliente aggiornati.",
            title="Cliente aggiornato",
        )
        return response

    @app.post("/customers/{customer_id}/delete", name="customer_delete_ui_feedback")
    def customer_delete_ui(
        request: Request,
        customer_id: uuid.UUID,
        confirm_name: str = Form(...),
        csrf: str = Form(...),
    ):
        try:
            response = crud.customer_delete(
                request=request,
                customer_id=customer_id,
                confirm_name=confirm_name,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _customer_error_feedback(request, customer_id, exc)
        location = str(response.headers.get("location") or "")
        if "error=confirm_name" in location:
            return flash_redirect(
                request,
                f"/customers/{customer_id}/edit",
                "warning",
                "Il nome di conferma non corrisponde al cliente. Nessun dato è stato eliminato.",
                title="Conferma eliminazione non valida",
            )
        add_flash(
            request,
            "success",
            "Cliente eliminato insieme ai dati dipendenti previsti dalla procedura.",
            title="Cliente eliminato",
        )
        return response

    @app.post(
        "/customers/{customer_id}/sites/{site_id}/edit",
        name="site_edit_ui_feedback",
    )
    def site_edit_ui(
        request: Request,
        customer_id: uuid.UUID,
        site_id: uuid.UUID,
        name: str = Form(...),
        address: str = Form(""),
        notes: str = Form(""),
        csrf: str = Form(...),
    ):
        try:
            response = crud.site_edit(
                request=request,
                customer_id=customer_id,
                site_id=site_id,
                name=name,
                address=address,
                notes=notes,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _site_error_feedback(request, customer_id, site_id, exc)
        add_flash(
            request,
            "success",
            "Dati della sede aggiornati.",
            title="Sede aggiornata",
        )
        return response

    @app.post(
        "/customers/{customer_id}/sites/{site_id}/delete",
        name="site_delete_ui_feedback",
    )
    def site_delete_ui(
        request: Request,
        customer_id: uuid.UUID,
        site_id: uuid.UUID,
        csrf: str = Form(...),
    ):
        try:
            response = crud.site_delete(
                request=request,
                customer_id=customer_id,
                site_id=site_id,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _site_error_feedback(request, customer_id, site_id, exc)
        add_flash(
            request,
            "success",
            "Sede eliminata; gli apparati associati restano nel cliente senza sede.",
            title="Sede eliminata",
        )
        return response

    _promote_named_routes(
        app,
        (
            "customer_edit_ui_feedback",
            "customer_delete_ui_feedback",
            "site_edit_ui_feedback",
            "site_delete_ui_feedback",
        ),
    )
