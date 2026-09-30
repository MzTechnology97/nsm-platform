"""Contextual browser feedback for Device CSV import validation.

The CSV importer remains the source of truth for parsing, row validation,
persistence and audit events. This adapter only keeps expected file/form
validation failures inside the import GUI instead of exposing raw FastAPI JSON
or 422 pages. Valid dry-run/import responses remain inline HTML so operators can
review row-level results before and after import.
"""
from __future__ import annotations

from fastapi import File, Form, HTTPException, Request, UploadFile

from app import device_csv_import as csv_import
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
    selected = []
    remaining = []
    for route in app.router.routes:
        if getattr(route, "name", None) == name:
            selected.append(route)
        else:
            remaining.append(route)
    app.router.routes[:] = selected + remaining


def _validation_feedback(request: Request, exc: HTTPException):
    if exc.status_code == 403:
        return flash_redirect(
            request,
            "/devices",
            "error",
            exception_message(exc, "Non hai i permessi necessari per importare apparati."),
            title="Operazione non autorizzata",
        )
    if exc.status_code in {400, 413, 422}:
        return flash_redirect(
            request,
            "/devices/import",
            "warning",
            exception_message(exc, "Controlla il file CSV e riprova."),
            title="CSV non valido",
        )
    raise exc


def install_ui_device_csv_import_feedback(app) -> None:
    """Install the browser adapter after the canonical CSV-import route."""

    _remove_post(app, "/devices/import")

    @app.post("/devices/import", name="device_csv_import_submit_ui_feedback")
    async def device_csv_import_submit_ui(
        request: Request,
        csv_file: UploadFile | None = File(None),
        mode: str = Form("validate"),
        csrf: str = Form(""),
    ):
        if csv_file is None or not str(csv_file.filename or "").strip():
            return flash_redirect(
                request,
                "/devices/import",
                "warning",
                "Seleziona un file CSV prima di avviare la verifica o l'importazione.",
                title="File CSV mancante",
            )

        try:
            return await csv_import.device_csv_import_submit(
                request=request,
                csv_file=csv_file,
                mode=mode,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _validation_feedback(request, exc)

    # FastAPI dispatches the first compatible route. Keep the exact browser
    # adapter ahead of any historical/import route so malformed input cannot
    # fall through to the raw JSON handler.
    _promote_named_route(app, "device_csv_import_submit_ui_feedback")
