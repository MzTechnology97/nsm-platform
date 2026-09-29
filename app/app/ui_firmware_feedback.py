"""Contextual browser feedback for firmware and RouterBOOT workflows.

This module only wraps human-facing POST routes. Domain handlers keep all
existing permission, capability, confirmation and audit checks; agent/API
endpoints remain untouched and continue returning structured JSON.
"""
from __future__ import annotations

import uuid

from fastapi import Form, HTTPException, Request

from app import mikrotik_firmware_readiness as firmware_readiness
from app import routerboot_lifecycle as routerboot
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


def _feedback_for_exception(request: Request, exc: HTTPException, return_to: str):
    if exc.status_code == 400:
        return flash_redirect(
            request,
            return_to,
            "warning",
            exception_message(exc, "Controlla i dati inseriti e riprova."),
            title="Dati non validi",
        )
    if exc.status_code == 403:
        return flash_redirect(
            request,
            return_to,
            "error",
            exception_message(exc, "Non hai i permessi necessari per questa operazione."),
            title="Operazione non autorizzata",
        )
    if exc.status_code == 409:
        return flash_redirect(
            request,
            return_to,
            "warning",
            exception_message(exc),
            title="Operazione non disponibile",
        )
    raise exc


def install_ui_firmware_feedback(app) -> None:
    """Install deterministic UI wrappers after the domain routers."""

    readiness_handler = firmware_readiness.queue_firmware_readiness
    stage_handler = routerboot.stage_routerboot
    reboot_handler = routerboot.reboot_routerboot

    for path in (
        "/devices/{device_id}/firmware-readiness",
        "/devices/{device_id}/routerboot/stage",
        "/devices/{device_id}/routerboot/reboot",
    ):
        _remove_post(app, path)

    @app.post(
        "/devices/{device_id}/firmware-readiness",
        name="queue_mikrotik_firmware_readiness",
    )
    def firmware_readiness_ui(
        request: Request,
        device_id: uuid.UUID,
        csrf: str = Form(...),
        return_to: str = Form("device"),
    ):
        destination = "/operations/firmware" if return_to == "firmware" else f"/devices/{device_id}"
        try:
            response = readiness_handler(
                request=request,
                device_id=device_id,
                csrf=csrf,
                return_to=return_to,
            )
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, destination)

        location = str(response.headers.get("location") or "")
        if "firmware_check=queued" in location:
            add_flash(
                request,
                "success",
                "Verifica firmware accodata. RouterOS verrà interrogato al prossimo heartbeat.",
                title="Verifica firmware accodata",
            )
        elif "firmware_check=already_queued" in location:
            add_flash(
                request,
                "info",
                "Una verifica firmware è già in coda o in esecuzione.",
                title="Verifica già richiesta",
            )
        elif "firmware_check=agent_required" in location:
            add_flash(
                request,
                "warning",
                "La verifica firmware richiede un MikroTik online con Agent autenticato.",
                title="Agent richiesto",
            )
        return response

    @app.post(
        "/devices/{device_id}/routerboot/stage",
        name="stage_routerboot_upgrade",
    )
    def routerboot_stage_ui(
        request: Request,
        device_id: uuid.UUID,
        csrf: str = Form(...),
        confirmation: str = Form(...),
    ):
        destination = f"/devices/{device_id}/routerboot"
        try:
            response = stage_handler(
                request=request,
                device_id=device_id,
                csrf=csrf,
                confirmation=confirmation,
            )
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, destination)
        add_flash(
            request,
            "success",
            "Staging RouterBOOT accodato. Nessun reboot viene eseguito in questa fase.",
            title="RouterBOOT staging accodato",
        )
        return response

    @app.post(
        "/devices/{device_id}/routerboot/reboot",
        name="reboot_for_routerboot_upgrade",
    )
    def routerboot_reboot_ui(
        request: Request,
        device_id: uuid.UUID,
        csrf: str = Form(...),
        confirmation: str = Form(...),
    ):
        destination = f"/devices/{device_id}/routerboot"
        try:
            response = reboot_handler(
                request=request,
                device_id=device_id,
                csrf=csrf,
                confirmation=confirmation,
            )
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, destination)
        add_flash(
            request,
            "warning",
            "Reboot RouterBOOT accodato. Il dispositivo diventerà temporaneamente non raggiungibile.",
            title="Reboot RouterBOOT accodato",
        )
        return response
