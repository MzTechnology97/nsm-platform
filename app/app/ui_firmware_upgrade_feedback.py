"""Contextual browser feedback for the RouterOS upgrade workflow.

This module wraps only human-facing POST actions.  The existing planner,
download-only staging and activation handlers remain authoritative for all
permissions, backup gates, exact confirmations, job creation, audit events and
post-reboot verification.  Agent/API endpoints are not changed.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from fastapi import Form, HTTPException, Request

from app import firmware_activation as activation
from app import firmware_package_staging as staging
from app import firmware_upgrade_planner as planner
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


def _promote_named_routes(app, names: Iterable[str]) -> None:
    ordered_names = list(names)
    selected = []
    remaining = []
    for route in app.router.routes:
        if getattr(route, "name", None) in ordered_names:
            selected.append(route)
        else:
            remaining.append(route)
    by_name = {}
    for route in selected:
        by_name.setdefault(getattr(route, "name", None), []).append(route)
    promoted = []
    for name in ordered_names:
        promoted.extend(by_name.get(name, []))
    app.router.routes[:] = promoted + remaining


def _feedback_for_exception(request: Request, exc: HTTPException, destination: str):
    if exc.status_code == 400:
        return flash_redirect(
            request,
            destination,
            "warning",
            exception_message(exc, "Controlla i dati inseriti e riprova."),
            title="Conferma non valida",
        )
    if exc.status_code == 403:
        return flash_redirect(
            request,
            destination,
            "error",
            exception_message(exc, "Non hai i permessi necessari per questa operazione."),
            title="Operazione non autorizzata",
        )
    if exc.status_code == 404:
        return flash_redirect(
            request,
            destination,
            "error",
            exception_message(exc, "Piano firmware non trovato."),
            title="Piano non disponibile",
        )
    if exc.status_code == 409:
        return flash_redirect(
            request,
            destination,
            "warning",
            exception_message(exc, "Il workflow firmware non può proseguire in questo momento."),
            title="Operazione non disponibile",
        )
    raise exc


def install_ui_firmware_upgrade_feedback(app) -> None:
    create_handler = planner.create_plan
    approve_handler = planner.approve_plan
    cancel_handler = planner.cancel_plan
    stage_handler = staging.stage_plan
    activate_handler = activation.activate_plan

    paths = (
        "/devices/{device_id}/firmware-upgrade/plan",
        "/devices/{device_id}/firmware-upgrade/{plan_id}/approve",
        "/devices/{device_id}/firmware-upgrade/{plan_id}/cancel",
        "/devices/{device_id}/firmware-upgrade/{plan_id}/stage",
        "/devices/{device_id}/firmware-upgrade/{plan_id}/activate",
    )
    for path in paths:
        _remove_post(app, path)

    @app.post(paths[0], name="create_firmware_upgrade_plan")
    def create_plan_ui(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
        fallback = f"/devices/{device_id}#firmware"
        try:
            response = create_handler(request=request, device_id=device_id, csrf=csrf)
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, fallback)
        add_flash(
            request,
            "info",
            "Piano firmware disponibile. Il backup pre-upgrade obbligatorio deve completarsi prima dell'approvazione.",
            title="Piano firmware pronto",
        )
        return response

    @app.post(paths[1], name="approve_firmware_upgrade_plan")
    def approve_plan_ui(
        request: Request,
        device_id: uuid.UUID,
        plan_id: uuid.UUID,
        csrf: str = Form(...),
        confirmation: str = Form(...),
    ):
        destination = f"/devices/{device_id}/firmware-upgrade?plan={plan_id}"
        try:
            approve_handler(
                request=request,
                device_id=device_id,
                plan_id=plan_id,
                csrf=csrf,
                confirmation=confirmation,
            )
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, destination)
        return flash_redirect(
            request,
            destination,
            "success",
            "Piano RouterOS approvato. L'approvazione non scarica pacchetti e non riavvia il dispositivo.",
            title="Piano firmware approvato",
        )

    @app.post(paths[2], name="cancel_firmware_upgrade_plan")
    def cancel_plan_ui(
        request: Request,
        device_id: uuid.UUID,
        plan_id: uuid.UUID,
        csrf: str = Form(...),
    ):
        destination = f"/devices/{device_id}/firmware-upgrade?plan={plan_id}"
        try:
            cancel_handler(request=request, device_id=device_id, plan_id=plan_id, csrf=csrf)
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, destination)
        return flash_redirect(
            request,
            destination,
            "info",
            "Piano firmware annullato. Nessuna nuova fase verrà accodata.",
            title="Piano annullato",
        )

    @app.post(paths[3], name="stage_firmware_upgrade_plan")
    def stage_plan_ui(
        request: Request,
        device_id: uuid.UUID,
        plan_id: uuid.UUID,
        csrf: str = Form(...),
        confirmation: str = Form(...),
    ):
        destination = f"/devices/{device_id}/firmware-upgrade?plan={plan_id}"
        try:
            stage_handler(
                request=request,
                device_id=device_id,
                plan_id=plan_id,
                csrf=csrf,
                confirmation=confirmation,
            )
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, destination)
        return flash_redirect(
            request,
            destination,
            "success",
            "Download dei pacchetti RouterOS accodato. Questa fase è download-only: non installa e non riavvia.",
            title="Staging firmware accodato",
        )

    @app.post(paths[4], name="activate_firmware_upgrade_plan")
    def activate_plan_ui(
        request: Request,
        device_id: uuid.UUID,
        plan_id: uuid.UUID,
        csrf: str = Form(...),
        confirmation: str = Form(...),
    ):
        destination = f"/devices/{device_id}/firmware-upgrade?plan={plan_id}"
        try:
            activate_handler(
                request=request,
                device_id=device_id,
                plan_id=plan_id,
                csrf=csrf,
                confirmation=confirmation,
            )
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, destination)
        return flash_redirect(
            request,
            destination,
            "warning",
            "Attivazione RouterOS accodata. Dopo il preflight l'apparato verrà riavviato e resterà temporaneamente non raggiungibile; il successo sarà confermato solo dal successivo inventario.",
            title="Attivazione firmware accodata",
        )

    _promote_named_routes(
        app,
        (
            "create_firmware_upgrade_plan",
            "approve_firmware_upgrade_plan",
            "cancel_firmware_upgrade_plan",
            "stage_firmware_upgrade_plan",
            "activate_firmware_upgrade_plan",
        ),
    )
