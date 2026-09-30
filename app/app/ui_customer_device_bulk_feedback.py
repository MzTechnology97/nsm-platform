"""Contextual feedback for customer-scoped Device bulk browser actions."""
from __future__ import annotations

import uuid

from fastapi import HTTPException, Request

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


def _bulk_error_feedback(request: Request, customer_id: uuid.UUID, exc: HTTPException):
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
            exception_message(exc, "Non hai i permessi necessari per modificare gli apparati."),
            title="Operazione non autorizzata",
        )
    if exc.status_code in {400, 409}:
        return flash_redirect(
            request,
            f"/customers/{customer_id}/devices/manage",
            "warning",
            exception_message(exc, "Controlla la selezione e riprova."),
            title="Operazione massiva non valida",
        )
    raise exc


def install_ui_customer_device_bulk_feedback(app) -> None:
    for path in (
        "/customers/{customer_id}/devices/bulk-delete",
        "/customers/{customer_id}/devices/bulk",
    ):
        _remove_post(app, path)

    @app.post(
        "/customers/{customer_id}/devices/bulk-delete",
        name="customer_device_bulk_delete_ui_feedback",
    )
    async def bulk_delete_ui(request: Request, customer_id: uuid.UUID):
        # Starlette caches parsed form data, so reading it here does not consume
        # the body for the existing CRUD handler below.
        form = await request.form()
        selected = crud._parse_device_ids(form.getlist("device_ids"))
        try:
            response = await crud.bulk_delete_devices(request, customer_id)
        except HTTPException as exc:
            return _bulk_error_feedback(request, customer_id, exc)
        if not selected:
            return flash_redirect(
                request,
                f"/customers/{customer_id}/devices/manage",
                "info",
                "Nessun apparato selezionato; non è stata eseguita alcuna eliminazione.",
                title="Nessun apparato selezionato",
            )
        add_flash(
            request,
            "success",
            f"Eliminazione massiva completata per {len(selected)} apparati selezionati.",
            title="Apparati eliminati",
        )
        return response

    @app.post(
        "/customers/{customer_id}/devices/bulk",
        name="customer_device_bulk_ui_feedback",
    )
    async def bulk_action_ui(request: Request, customer_id: uuid.UUID):
        form = await request.form()
        action = str(form.get("action", "")).strip()
        selected = crud._parse_device_ids(form.getlist("device_ids"))
        try:
            response = await crud.customer_devices_bulk(request, customer_id)
        except HTTPException as exc:
            return _bulk_error_feedback(request, customer_id, exc)

        if action == "move":
            add_flash(
                request,
                "success",
                f"Spostamento completato per {len(selected)} apparati.",
                title="Apparati spostati",
            )
        elif action == "delete":
            add_flash(
                request,
                "success",
                f"Eliminazione massiva completata per {len(selected)} apparati.",
                title="Apparati eliminati",
            )
        else:
            add_flash(
                request,
                "success",
                "Operazione massiva completata.",
                title="Operazione completata",
            )
        return response

    _promote_named_routes(
        app,
        (
            "customer_device_bulk_delete_ui_feedback",
            "customer_device_bulk_ui_feedback",
        ),
    )
