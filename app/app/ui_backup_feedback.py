"""Contextual browser feedback for manual backup actions.

Only the human-facing ``Backup ora`` route is wrapped here.  The existing
MikroTik backup domain handler remains authoritative for permissions, policy
resolution, job creation, audit events and secret generation.  Agent-facing
backup config/upload/completion endpoints are intentionally untouched.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, Request

from app import mikrotik_backup as backup
from app.ui_feedback import exception_message, flash_redirect


def _remove_post(app, path: str) -> None:
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and "POST" in (getattr(route, "methods", set()) or set())
        )
    ]


def _promote_named_route(app, name: str) -> None:
    selected = [route for route in app.router.routes if getattr(route, "name", None) == name]
    if not selected:
        raise RuntimeError(f"Backup UI route not registered: {name}")
    remaining = [route for route in app.router.routes if getattr(route, "name", None) != name]
    app.router.routes[:] = selected + remaining


def _feedback_for_exception(request: Request, exc: HTTPException, destination: str):
    if exc.status_code == 400:
        return flash_redirect(
            request,
            destination,
            "warning",
            exception_message(exc, "Il backup non può essere avviato con i dati correnti."),
            title="Backup non disponibile",
        )
    if exc.status_code == 403:
        return flash_redirect(
            request,
            destination,
            "error",
            exception_message(exc, "Non hai i permessi necessari per avviare il backup."),
            title="Operazione non autorizzata",
        )
    if exc.status_code == 409:
        return flash_redirect(
            request,
            destination,
            "warning",
            exception_message(exc, "Il backup non può essere avviato in questo momento."),
            title="Backup non disponibile",
        )
    raise exc


def install_ui_backup_feedback(app) -> None:
    """Replace and promote the manual backup browser route."""

    backup_handler = backup.queue_mikrotik_backup
    path = "/devices/{device_id}/backup-now"
    _remove_post(app, path)

    @app.post(path, name="queue_mikrotik_backup_ui")
    async def backup_now_ui(request: Request, device_id: uuid.UUID):
        destination = f"/devices/{device_id}#backups"
        try:
            response = await backup_handler(request=request, device_id=device_id)
        except HTTPException as exc:
            return _feedback_for_exception(request, exc, destination)

        location = str(response.headers.get("location") or "")
        if "backup=already_pending" in location:
            return flash_redirect(
                request,
                destination,
                "info",
                "È già presente un backup in attesa o in esecuzione per questo apparato.",
                title="Backup già in corso",
            )
        if "backup=queued" in location:
            return flash_redirect(
                request,
                destination,
                "success",
                "Backup accodato. L'Agent MikroTik lo eseguirà al prossimo heartbeat.",
                title="Backup accodato",
            )
        return response

    # Installed after the historical routers, then promoted so a compatible
    # legacy/generic route can never intercept the browser action first.
    _promote_named_route(app, "queue_mikrotik_backup_ui")
