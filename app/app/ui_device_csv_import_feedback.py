"""Contextual error feedback for the browser Device CSV importer."""
from __future__ import annotations

from fastapi import HTTPException, Request

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


def _csv_error_feedback(request: Request, exc: HTTPException):
    if exc.status_code == 403:
        return flash_redirect(
            request,
            "/devices",
            "error",
            exception_message(exc, "Solo gli amministratori possono importare inventario CSV."),
            title="Operazione non autorizzata",
        )
    if exc.status_code in {400, 413, 415, 422}:
        return flash_redirect(
            request,
            "/devices/import",
            "warning",
            exception_message(exc, "Il file CSV non può essere elaborato."),
            title="CSV non valido",
        )
    raise exc


def install_ui_device_csv_import_feedback(app) -> None:
    _remove_post(app, "/devices/import")

    @app.post("/devices/import", name="device_csv_import_submit_ui_feedback")
    async def device_csv_import_submit_ui(request: Request):
        # Parse the multipart form ourselves so a missing upload/mode/CSRF field
        # remains a normal browser validation result instead of FastAPI's raw
        # framework-level 422 JSON response.
        try:
            form = await request.form()
            csv_file = form.get("csv_file")
            if csv_file is None or not callable(getattr(csv_file, "read", None)):
                raise HTTPException(400, "Seleziona un file CSV da importare.")
            mode = str(form.get("mode", "validate") or "validate")
            csrf = str(form.get("csrf", "") or "")
            return await csv_import.device_csv_import_submit(
                request=request,
                csv_file=csv_file,
                mode=mode,
                csrf=csrf,
            )
        except HTTPException as exc:
            return _csv_error_feedback(request, exc)

    _promote_named_route(app, "device_csv_import_submit_ui_feedback")
