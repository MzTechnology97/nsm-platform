"""Admin API for the bulk CPE → self-hosted ACS migration (``users.manage``).

- ``GET  /api/ui/v1/admin/acs/migration``              — defaults, the active run and recent runs.
- ``POST /api/ui/v1/admin/acs/migration``              — start a run (dry run unless ``confirm`` is true).
- ``GET  /api/ui/v1/admin/acs/migration/{run_id}``     — one run with its per-host results.
- ``POST /api/ui/v1/admin/acs/migration/{run_id}/cancel`` — stop the active run.

The credential is read from the request, passed to the run in memory, and never stored or echoed back.
"""
from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app import acs_admin
from app import acs_migration as mig
from app.acs_migration_models import AcsMigrationHost, AcsMigrationRun
from app.db import SessionLocal
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1/admin/acs", tags=["ui"])
RECENT_RUNS = 10
MAX_HOST_ROWS = 4096


def _public_host(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.hostname or ""
    return urlsplit(f"//{host}").hostname or ""


def _suggested_target(request: Request) -> str:
    return acs_admin.cpe_acs_url(request).rstrip("/")


def _skip_set(request: Request) -> set[str]:
    """Addresses the run must never touch: the NSM/ACS host itself."""
    skip = set()
    host = _public_host(request)
    if host:
        skip.add(host)
    return skip


@router.get("/migration")
def migration_state(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        active_id = mig.active_run_id()
        runs = list(db.scalars(select(AcsMigrationRun).order_by(AcsMigrationRun.created_at.desc()).limit(RECENT_RUNS)))
        return jsonable_encoder({
            "defaults": {"vendor": "tp-link", "port": 443, "inform_interval": 300, "target_acs_url": _suggested_target(request),
                         "max_hosts": mig.DEFAULT_MAX_HOSTS, "hard_max_hosts": mig.MAX_HOSTS, "min_inform_interval": mig.MIN_INFORM_INTERVAL},
            "vendors": list(mig.VENDORS),
            "apply_available": False,  # the authenticated CWMP rewrite is in field validation
            "active_run_id": active_id,
            "runs": [mig.run_summary(run) for run in runs],
        })


@router.post("/migration", dependencies=[Depends(require_csrf)])
async def start_migration(request: Request):
    body = await request.json()
    confirm = bool(body.get("confirm"))
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        try:
            target = mig.normalize_target_url(body.get("target_acs_url") or _suggested_target(request))
            if confirm and not (username and password):
                raise mig.MigrationInputError("credentials", "Per applicare la migrazione servono le credenziali dei router.")
            run = mig.start_run(
                user=user, vendor=str(body.get("vendor") or "tp-link"), subnet=str(body.get("subnet") or ""),
                port=int(body.get("port") or 443), target_acs_url=target, inform_interval=int(body.get("inform_interval") or 300),
                credential=mig.Credential(username=username, password=password), dry_run=not confirm,
                skip=_skip_set(request),
            )
        except mig.MigrationInputError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc)}) from None
        except (TypeError, ValueError):
            raise HTTPException(422, {"code": "input", "message": "Parametri non validi."}) from None
        return jsonable_encoder({"run": mig.run_summary(run)})


@router.get("/migration/{run_id}")
def migration_run(request: Request, run_id: uuid.UUID):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        run = db.get(AcsMigrationRun, run_id)
        if run is None:
            raise HTTPException(404)
        hosts = list(db.scalars(select(AcsMigrationHost).where(AcsMigrationHost.run_id == run_id)
                                .order_by(AcsMigrationHost.address).limit(MAX_HOST_ROWS)))
        return jsonable_encoder({"run": mig.run_summary(run), "hosts": [mig.host_summary(h) for h in hosts],
                                 "active": mig.active_run_id() == str(run_id)})


@router.post("/migration/{run_id}/cancel", dependencies=[Depends(require_csrf)])
def cancel_migration(request: Request, run_id: uuid.UUID):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        if db.get(AcsMigrationRun, run_id) is None:
            raise HTTPException(404)
    return {"cancelled": mig.cancel_run(str(run_id))}


def install_acs_migration(app) -> None:
    app.include_router(router)
