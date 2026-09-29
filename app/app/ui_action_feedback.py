"""Browser-only action wrappers using contextual flash feedback.

The underlying domain functions keep their existing behavior and machine-facing
API contracts.  This installer replaces only human POST routes and is loaded at
the end of the application bootstrap so its routes have canonical precedence.
"""
from __future__ import annotations

import uuid

from fastapi import Form, HTTPException, Request

from app import mikrotik_agent_update as agent_update
from app import mikrotik_snapshot_batch as snapshot_batch
from app import mikrotik_workspace as workspace
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


def install_ui_action_feedback(app) -> None:
    # Route precedence matters in FastAPI: remove the human POST routes and
    # re-register wrappers after all feature installers have completed.
    for path in (
        "/devices/{device_id}/snapshot/{section}",
        "/devices/{device_id}/snapshot-all",
        "/devices/{device_id}/diagnostics/{diagnostic}",
        "/devices/{device_id}/agent/update",
    ):
        _remove_post(app, path)

    @app.post("/devices/{device_id}/snapshot/{section}", name="queue_mikrotik_snapshot_ui")
    def snapshot_ui(
        request: Request,
        device_id: uuid.UUID,
        section: str,
        csrf: str = Form(...),
    ):
        return_to = f"/devices/{device_id}/configuration?section={section}"
        try:
            response = workspace.queue_snapshot(request, device_id, section, csrf)
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, return_to)
        location = str(response.headers.get("location") or "")
        if "queued=already" in location:
            add_flash(request, "info", "La sezione è già in coda o in esecuzione.", title="Snapshot già richiesto")
        else:
            add_flash(request, "success", "Aggiornamento della sezione accodato. Verrà eseguito al prossimo heartbeat.", title="Snapshot accodato")
        return response

    @app.post("/devices/{device_id}/snapshot-all", name="queue_mikrotik_snapshot_all_ui")
    def snapshot_all_ui(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
        return_to = f"/devices/{device_id}/configuration?section=resources"
        try:
            response = snapshot_batch.queue_snapshot_all(request, device_id, csrf)
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, return_to)
        location = str(response.headers.get("location") or "")
        if "queued=already" in location:
            add_flash(request, "info", "Le sezioni risultano già in coda o in esecuzione.", title="Snapshot già richiesto")
        else:
            add_flash(request, "success", "Aggiornamento completo accodato come job separati per ogni sezione.", title="Snapshot completo accodato")
        return response

    @app.post("/devices/{device_id}/diagnostics/{diagnostic}", name="queue_mikrotik_diagnostic_ui")
    def diagnostic_ui(
        request: Request,
        device_id: uuid.UUID,
        diagnostic: str,
        target: str = Form(""),
        source: str = Form(""),
        query: str = Form(""),
        csrf: str = Form(...),
    ):
        return_to = f"/devices/{device_id}/diagnostics"
        try:
            response = workspace.queue_diagnostic(
                request,
                device_id,
                diagnostic,
                target,
                source,
                query,
                csrf,
            )
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, return_to)
        label = {
            "ping": "Ping",
            "traceroute": "Traceroute",
            "neighbors": "Neighbor discovery",
            "dhcp_lookup": "Ricerca DHCP",
            "logs": "Log diagnostici",
            "support_snapshot": "Support snapshot",
        }.get(diagnostic, "Diagnostica")
        add_flash(request, "success", f"{label} accodato. Il risultato comparirà in Job & attività.", title="Diagnostica accodata")
        return response

    @app.post("/devices/{device_id}/agent/update", name="queue_mikrotik_agent_update_ui")
    def agent_update_ui(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
        return_to = f"/devices/{device_id}/agent"
        try:
            response = agent_update.queue_agent_update(request, device_id, csrf)
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, return_to)
        add_flash(request, "success", "Aggiornamento Agent accodato. Verrà applicato dal dispositivo con rollback automatico in caso di errore.", title="Aggiornamento Agent accodato")
        return response
