"""Browser-only action wrappers using contextual flash feedback.

The UI layer deliberately binds to known guarded domain handlers instead of
selecting the first matching FastAPI route. Browser validation uses the same
domain validators before queueing so expected input failures are rendered as
contextual PRG feedback, while the underlying handler repeats validation as a
second server-side barrier. Machine-facing API/agent endpoints remain untouched.

Supported snapshot and diagnostic actions also receive concrete static POST
routes which are promoted ahead of historical generic FastAPI routes. This
makes browser routing deterministic even when older route templates remain in
the application for compatibility.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from fastapi import Form, HTTPException, Request

from app import mikrotik_agent_update as agent_update
from app import mikrotik_snapshot_batch as snapshot_batch
from app import mikrotik_workspace as workspace
from app import mikrotik_workspace_ux as workspace_ux
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
    """Move selected UI routes to the front while preserving explicit order."""
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


def _prevalidate_diagnostic(diagnostic: str, target: str, source: str, query: str) -> None:
    if diagnostic not in workspace.DIAGNOSTIC_TYPES:
        raise HTTPException(400, "Diagnostica non supportata.")
    if diagnostic in {"ping", "traceroute"}:
        workspace._safe_target(target)
        workspace._safe_source(source)
    elif diagnostic == "dhcp_lookup":
        workspace._safe_dhcp_query(query)


def install_ui_action_feedback(app) -> None:
    for path in (
        "/devices/{device_id}/snapshot/{section}",
        "/devices/{device_id}/snapshot-all",
        "/devices/{device_id}/diagnostics/{diagnostic}",
        "/devices/{device_id}/agent/update",
    ):
        _remove_post(app, path)

    # Bind explicitly to capability-aware handlers. Do not infer handlers from
    # route order: older registrations may share compatible URL templates.
    snapshot_handler = workspace_ux._guard_snapshot
    snapshot_all_handler = snapshot_batch.queue_snapshot_all
    diagnostic_handler = workspace_ux._guard_diagnostic
    agent_update_handler = agent_update.queue_agent_update

    def snapshot_action(request: Request, device_id: uuid.UUID, section: str, csrf: str):
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
            add_flash(
                request,
                "info",
                "La sezione è già in coda o in esecuzione.",
                title="Snapshot già richiesto",
            )
        else:
            add_flash(
                request,
                "success",
                "Aggiornamento della sezione accodato. Verrà eseguito al prossimo heartbeat.",
                title="Snapshot accodato",
            )
        return response

    def diagnostic_action(
        request: Request,
        device_id: uuid.UUID,
        diagnostic: str,
        target: str,
        source: str,
        query: str,
        csrf: str,
    ):
        return_to = f"/devices/{device_id}/diagnostics"
        try:
            _prevalidate_diagnostic(diagnostic, target, source, query)
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
        add_flash(
            request,
            "success",
            f"{label} accodato. Il risultato comparirà in Job & attività.",
            title="Diagnostica accodata",
        )
        return response

    @app.post("/devices/{device_id}/snapshot/{section}", name="queue_mikrotik_snapshot")
    def snapshot_ui(
        request: Request,
        device_id: uuid.UUID,
        section: str,
        csrf: str = Form(...),
    ):
        return snapshot_action(request, device_id, section, csrf)

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
            add_flash(
                request,
                "info",
                "Le sezioni risultano già in coda o in esecuzione.",
                title="Snapshot già richiesto",
            )
        else:
            add_flash(
                request,
                "success",
                "Aggiornamento completo accodato come job separati per ogni sezione.",
                title="Snapshot completo accodato",
            )
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
        return diagnostic_action(request, device_id, diagnostic, target, source, query, csrf)

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
        add_flash(
            request,
            "success",
            "Aggiornamento Agent accodato. Verrà applicato dal dispositivo con rollback automatico in caso di errore.",
            title="Aggiornamento Agent accodato",
        )
        return response

    # Concrete routes win over any historical generic template such as
    # /diagnostics/{diagnostic}.  Keep the generic routes above as a fallback
    # for compatibility and for explicit unsupported-action feedback.
    priority_names = []

    def make_snapshot_static(section: str):
        def snapshot_static(
            request: Request,
            device_id: uuid.UUID,
            csrf: str = Form(...),
        ):
            return snapshot_action(request, device_id, section, csrf)

        snapshot_static.__name__ = f"snapshot_{section}_ui"
        return snapshot_static

    for section in workspace.SNAPSHOT_SECTIONS:
        route_name = f"queue_mikrotik_snapshot_{section}_ui"
        app.add_api_route(
            f"/devices/{{device_id}}/snapshot/{section}",
            make_snapshot_static(section),
            methods=["POST"],
            name=route_name,
        )
        priority_names.append(route_name)

    def make_diagnostic_static(diagnostic: str):
        def diagnostic_static(
            request: Request,
            device_id: uuid.UUID,
            target: str = Form(""),
            source: str = Form(""),
            query: str = Form(""),
            csrf: str = Form(...),
        ):
            return diagnostic_action(
                request,
                device_id,
                diagnostic,
                target,
                source,
                query,
                csrf,
            )

        diagnostic_static.__name__ = f"diagnostic_{diagnostic}_ui"
        return diagnostic_static

    for diagnostic in workspace.DIAGNOSTIC_TYPES:
        route_name = f"queue_mikrotik_diagnostic_{diagnostic}_ui"
        app.add_api_route(
            f"/devices/{{device_id}}/diagnostics/{diagnostic}",
            make_diagnostic_static(diagnostic),
            methods=["POST"],
            name=route_name,
        )
        priority_names.append(route_name)

    # Static actions first, then the two exact singleton actions, then generic
    # compatibility wrappers. GET routes and all machine-facing endpoints keep
    # their existing relative order after this UI-only prefix.
    priority_names.extend(
        [
            "queue_mikrotik_snapshot_all",
            "queue_mikrotik_agent_update",
            "queue_mikrotik_snapshot",
            "queue_mikrotik_diagnostic",
        ]
    )
    _promote_named_routes(app, priority_names)
