"""Contextual feedback for browser-only Notification Center read actions."""
from __future__ import annotations

import uuid

from fastapi import Form, HTTPException, Request

from app import main as core
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


def _feedback(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        return core.login_redirect()
    if exc.status_code == 403:
        return flash_redirect(
            request,
            "/notifications",
            "error",
            exception_message(exc, "La richiesta non può essere completata."),
            title="Operazione non autorizzata",
        )
    if exc.status_code == 404:
        return flash_redirect(
            request,
            "/notifications",
            "warning",
            exception_message(exc, "La notifica non è più disponibile."),
            title="Notifica non disponibile",
        )
    raise exc


def install_ui_notification_feedback(app) -> None:
    for path in (
        "/notifications/{notification_id}/read",
        "/notifications/read-all",
    ):
        _remove_post(app, path)

    @app.post(
        "/notifications/{notification_id}/read",
        name="notification_read_ui_feedback",
    )
    def mark_notification_read_ui(
        request: Request,
        notification_id: uuid.UUID,
        csrf: str = Form(""),
    ):
        try:
            response = core.mark_notification_read(
                request=request,
                notification_id=notification_id,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _feedback(request, exc)
        add_flash(
            request,
            "success",
            "Notifica segnata come letta.",
            title="Notifica aggiornata",
        )
        return response

    @app.post("/notifications/read-all", name="notifications_read_all_ui_feedback")
    def mark_all_notifications_read_ui(
        request: Request,
        csrf: str = Form(""),
    ):
        try:
            response = core.mark_all_notifications_read(
                request=request,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _feedback(request, exc)
        add_flash(
            request,
            "success",
            "Tutte le notifiche attive sono state segnate come lette.",
            title="Notifiche aggiornate",
        )
        return response

    _promote_named_routes(
        app,
        ("notification_read_ui_feedback", "notifications_read_all_ui_feedback"),
    )
