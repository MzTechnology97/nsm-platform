"""Browser-only action wrappers using contextual flash feedback.

The installer captures the canonical POST handler already registered by the
feature stack, removes only that browser route, and re-registers a feedback
wrapper.  This preserves all capability/compatibility guards while leaving
machine-facing API/agent endpoints untouched.
"""
from __future__ import annotations

import uuid

from fastapi import Form, HTTPException, Request

from app import mikrotik_snapshot_batch as snapshot_batch
from app.ui_feedback import add_flash, exception_message, flash_redirect


def _take_post(app, path: str, fallback=None):
    endpoint = None
    kept = []
    for route in app.router.routes:
        match = (
            getattr(route, "path", None) == path
            and "POST" in (getattr(route, "methods", set()) or set())
        )
        if match:
            if endpoint is None:
                endpoint = getattr(route, "endpoint", None)
            continue
        kept.append(route)
    app.router.routes[:] = kept
    if endpoint is None:
        if fallback is None:
            raise RuntimeError(f"Browser action route not found: POST {path}")
        endpoint = fallback
    return endpoint


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
    snapshot_handler = _take_post(app, "/devices/{device_id}/snapshot/{section}")
    # The unified configuration route stack in Core 0.49 can lose the batch
    # route while promoting canonical workspace routes.  The batch handler is
    # itself the capability guard (online + modern transport), so using it as
    # the explicit fallback both restores the advertised UI action and keeps
    # the same safety contract.
    snapshot_all_handler = _take_post(
        app,
        "/devices/{device_id}/snapshot-all",
        fallback=snapshot_batch.queue_snapshot_all,
    )
    diagnostic_handler = _take_post(app, "/devices/{device_id}/diagnostics/{diagnostic}")
    agent_update_handler = _take_post(app, "/devices/{device_id}/agent/update")

    @app.post("/devices/{device_id}/snapshot/{section}", name="queue_mikrotik_snapshot")
    def snapshot_ui(
        request: Request,
        device_id: uuid.UUID,
        section: str,
        csrf: str = Form(...),
    ):
        return_to = f"/devices/{device_id}/configuration?section={section}"
        try:
            response = snapshot_handler(
                request=request,
                device_id=device_id,
                section=section,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, return_to)
        location = str(response.headers.get("location") or "")
        if "queued=already" in location:
            add_flash(request, "info", "La sezione è già in coda o in esecuzione.", title="Snapshot già richiesto")
        else:
            add_flash(request, "success", "Aggiornamento della sezione accodato. Verrà eseguito al prossimo heartbeat.", title="Snapshot accodato")
        return response

    @app.post("/devices/{device_id}/snapshot-all", name="queue_mikrotik_snapshot_all")
    def snapshot_all_ui(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
        return_to = f"/devices/{device_id}/configuration?section=resources"
        try:
            response = snapshot_all_handler(
                request=request,
                device_id=device_id,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, return_to)
        location = str(response.headers.get("location") or "")
        if "queued=already" in location:
            add_flash(request, "info", "Le sezioni risultano già in coda o in esecuzione.", title="Snapshot già richiesto")
        else:
            add_flash(request, "success", "Aggiornamento completo accodato come job separati per ogni sezione.", title="Snapshot completo accodato")
        return response

    @app.post("/devices/{device_id}/diagnostics/{diagnostic}", name="queue_mikrotik_diagnostic")
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
            response = diagnostic_handler(
                request=request,
                device_id=device_id,
                diagnostic=diagnostic,
                target=target,
                source=source,
                query=query,
                csrf=csrf,
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

    @app.post("/devices/{device_id}/agent/update", name="queue_mikrotik_agent_update")
    def agent_update_ui(request: Request, device_id: uuid.UUID, csrf: str = Form(...)):
        return_to = f"/devices/{device_id}/agent"
        try:
            response = agent_update_handler(
                request=request,
                device_id=device_id,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, return_to)
        add_flash(request, "success", "Aggiornamento Agent accodato. Verrà applicato dal dispositivo con rollback automatico in caso di errore.", title="Aggiornamento Agent accodato")
        return response
