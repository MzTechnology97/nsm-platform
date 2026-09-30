"""Contextual feedback for browser-only Action Center acknowledgement.

The core ActionIssue handler remains the source of truth for permissions,
state transitions, audit events and persistence. This adapter only keeps
expected browser failures inside the GUI instead of exposing raw FastAPI JSON.
"""
from __future__ import annotations

import uuid

from fastapi import Form, HTTPException, Request

from app import main as core
from app.db import SessionLocal
from app.models import ActionIssue
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


def _promote_named_route(app, name: str) -> None:
    selected = [route for route in app.router.routes if getattr(route, "name", None) == name]
    remaining = [route for route in app.router.routes if getattr(route, "name", None) != name]
    app.router.routes[:] = selected + remaining


def _feedback(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        return core.login_redirect()
    if exc.status_code == 403:
        return flash_redirect(
            request,
            "/action-center",
            "error",
            exception_message(exc, "Non hai i permessi necessari per prendere in carico questa attenzione."),
            title="Operazione non autorizzata",
        )
    if exc.status_code == 404:
        return flash_redirect(
            request,
            "/action-center",
            "warning",
            exception_message(exc, "L'attenzione non è più disponibile."),
            title="Attenzione non disponibile",
        )
    raise exc


def install_ui_action_center_feedback(app) -> None:
    path = "/action-center/{issue_id}/ack"
    _remove_post(app, path)

    @app.post(path, name="action_center_ack_ui_feedback")
    def acknowledge_issue_ui(
        request: Request,
        issue_id: uuid.UUID,
        csrf: str = Form(""),
    ):
        with SessionLocal() as db:
            issue = db.get(ActionIssue, issue_id)
            was_acknowledged = bool(issue and issue.status == "acknowledged")

        try:
            response = core.acknowledge_issue(
                request=request,
                issue_id=issue_id,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _feedback(request, exc)

        if was_acknowledged:
            add_flash(
                request,
                "info",
                "L'attenzione risultava già presa in carico; non è stata eseguita una seconda modifica.",
                title="Attenzione già presa in carico",
            )
        else:
            add_flash(
                request,
                "success",
                "Attenzione presa in carico. Lo stato è stato aggiornato nell'Action Center.",
                title="Attenzione presa in carico",
            )
        return response

    _promote_named_route(app, "action_center_ack_ui_feedback")
