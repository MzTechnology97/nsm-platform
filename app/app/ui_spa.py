"""Serves the new React interface under ``/app`` (gradual migration).

The production build lives in ``NSM_UI_DIST`` (default ``app/static/ui``, filled
by the Docker image).  Hashed assets are served by the ``/static`` mount with
long caching; ``index.html`` is returned for every ``/app/...`` path so the
client router can handle deep links, and is never cached.  Without a build the
route explains how to produce it instead of failing silently.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount

from app import main as core
from app.db import SessionLocal

DEFAULT_DIST = Path(__file__).resolve().parent / "static" / "ui"
DEV_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def dist_dir() -> Path:
    """Image build (app/static/ui), else a local ``npm run build`` (app/frontend/dist)."""
    if os.getenv("NSM_UI_DIST"):
        return Path(os.environ["NSM_UI_DIST"])
    return DEFAULT_DIST if DEFAULT_DIST.is_dir() or not DEV_DIST.is_dir() else DEV_DIST


def spa(request: Request, path: str = ""):
    with SessionLocal() as db:
        if not core.current_user(request, db):
            return RedirectResponse("/login", status_code=303)
    index = dist_dir() / "index.html"
    if not index.is_file():
        return HTMLResponse("<!doctype html><meta charset=utf-8><title>NSM</title><p>La nuova interfaccia non è stata compilata: "
                            "ricostruisci l'immagine con <code>./update.sh</code> (oppure <code>npm run build</code> in app/frontend).</p>"
                            "<p><a href=\"/\">Torna all'interfaccia classica</a></p>", status_code=503)
    return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache"})


def install_ui_spa(app) -> None:
    # Hashed assets of the build; placed before the generic /static mount, which would shadow it.
    app.router.routes.insert(0, Mount("/static/ui", app=StaticFiles(directory=str(dist_dir()), check_dir=False), name="ui_assets"))
    app.add_api_route("/app", spa, methods=["GET"], include_in_schema=False, name="ui_app")
    app.add_api_route("/app/{path:path}", spa, methods=["GET"], include_in_schema=False, name="ui_app_path")
