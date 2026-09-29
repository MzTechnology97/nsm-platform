"""Contextual browser feedback for Backup Center policy actions.

This layer intentionally wraps only simple state-changing policy actions whose
natural destination is the Backup Center.  Policy create/edit stay on their
existing form workflow so a later change can preserve submitted values when
validation fails.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from fastapi import HTTPException, Request

from app import backup_core
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
    if len(promoted) != len(ordered_names):
        missing = [name for name in ordered_names if name not in by_name]
        raise RuntimeError(f"Backup policy UI routes not registered: {', '.join(missing)}")
    app.router.routes[:] = promoted + remaining


def _feedback_for_exception(request: Request, exc: HTTPException):
    destination = "/operations/backups"
    if exc.status_code == 400:
        return flash_redirect(
            request,
            destination,
            "warning",
            exception_message(exc, "Controlla i dati inseriti e riprova."),
            title="Dati non validi",
        )
    if exc.status_code == 403:
        return flash_redirect(
            request,
            destination,
            "error",
            exception_message(exc, "Non hai i permessi necessari per modificare le policy di backup."),
            title="Operazione non autorizzata",
        )
    if exc.status_code == 404:
        return flash_redirect(
            request,
            destination,
            "error",
            exception_message(exc, "La policy richiesta non è più disponibile."),
            title="Policy non trovata",
        )
    if exc.status_code == 409:
        return flash_redirect(
            request,
            destination,
            "warning",
            exception_message(exc),
            title="Operazione non disponibile",
        )
    raise exc


def install_ui_backup_policy_feedback(app) -> None:
    """Replace and promote toggle/delete browser routes for backup policies."""

    toggle_handler = backup_core.backup_policy_toggle
    delete_handler = backup_core.backup_policy_delete

    for path in (
        "/operations/backups/policies/{policy_id}/toggle",
        "/operations/backups/policies/{policy_id}/delete",
    ):
        _remove_post(app, path)

    @app.post(
        "/operations/backups/policies/{policy_id}/toggle",
        name="backup_policy_toggle_ui",
    )
    async def backup_policy_toggle_ui(request: Request, policy_id: uuid.UUID):
        try:
            await toggle_handler(request=request, policy_id=policy_id)
        except HTTPException as exc:
            return _feedback_for_exception(request, exc)
        return flash_redirect(
            request,
            "/operations/backups",
            "success",
            "Lo stato della policy di backup è stato aggiornato.",
            title="Policy aggiornata",
        )

    @app.post(
        "/operations/backups/policies/{policy_id}/delete",
        name="backup_policy_delete_ui",
    )
    async def backup_policy_delete_ui(request: Request, policy_id: uuid.UUID):
        try:
            await delete_handler(request=request, policy_id=policy_id)
        except HTTPException as exc:
            return _feedback_for_exception(request, exc)
        return flash_redirect(
            request,
            "/operations/backups",
            "success",
            "La policy di backup è stata eliminata.",
            title="Policy eliminata",
        )

    _promote_named_routes(
        app,
        (
            "backup_policy_toggle_ui",
            "backup_policy_delete_ui",
        ),
    )
