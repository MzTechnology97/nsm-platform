"""Administration of the NVD advisory source (SEC-01)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app import advisory_sources as sources
from app import main as core
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import DeviceVulnerability, SecurityAdvisory, utcnow
from app.secret_vault import encrypt_text
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
PAGE = "/admin/integrations/nvd"


def _render(request, db, user):
    connection = sources.connection_for(db)
    advisory_count = int(
        db.scalar(select(func.count(SecurityAdvisory.id)).where(SecurityAdvisory.source == sources.PROVIDER)) or 0
    )
    open_findings = int(
        db.scalar(
            select(func.count(DeviceVulnerability.id))
            .join(SecurityAdvisory, SecurityAdvisory.id == DeviceVulnerability.advisory_id)
            .where(SecurityAdvisory.source == sources.PROVIDER, DeviceVulnerability.status != "resolved")
        )
        or 0
    )
    return core.render(
        request,
        db,
        user,
        "admin_nvd.html",
        connection=connection,
        sync=sources.sync_state(connection),
        matching=((connection.settings or {}).get("matching") or {}) if connection else {},
        interval=sources.interval_minutes(connection) if connection else sources.INTERVAL_DEFAULT_MINUTES,
        has_key=bool(connection and connection.secret_encrypted),
        advisory_count=advisory_count,
        open_findings=open_findings,
        tracked_cpes=sources.sync_state(connection).get("tracked_cpes") or list(sources.TRACKED_CPES),
        default_url=sources.NVD_DEFAULT_URL,
        interval_min=sources.INTERVAL_MIN_MINUTES,
        interval_max=sources.INTERVAL_MAX_MINUTES,
    )


@router.get(PAGE, response_class=HTMLResponse, name="admin_nvd")
def admin_nvd(request: Request):
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        return _render(request, db, user)


@router.post(PAGE, name="admin_nvd_save")
def admin_nvd_save(
    request: Request,
    csrf: str = Form(...),
    is_enabled: str | None = Form(None),
    api_key: str = Form(""),
    clear_api_key: str | None = Form(None),
    sync_interval_minutes: str = Form(""),
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        try:
            interval = int(sync_interval_minutes or sources.INTERVAL_DEFAULT_MINUTES)
        except ValueError:
            interval = -1
        if not sources.INTERVAL_MIN_MINUTES <= interval <= sources.INTERVAL_MAX_MINUTES:
            return flash_redirect(
                request,
                PAGE,
                "warning",
                f"L'intervallo deve essere tra {sources.INTERVAL_MIN_MINUTES} e {sources.INTERVAL_MAX_MINUTES} minuti.",
                title="Dati non validi",
            )
        connection = sources.connection_for(db)
        created = connection is None
        if created:
            connection = ConnectorIntegration(
                provider=sources.PROVIDER,
                name="NVD · National Vulnerability Database",
                base_url=sources.NVD_DEFAULT_URL,
                secret_encrypted="",
                verify_tls=True,
                settings={},
            )
            db.add(connection)
        connection.is_enabled = is_enabled is not None
        key = api_key.strip()
        if key:
            connection.secret_encrypted = encrypt_text(key)
        elif clear_api_key is not None:
            connection.secret_encrypted = ""
        settings = dict(connection.settings or {})
        settings["sync_interval_minutes"] = interval
        connection.settings = settings
        core.add_event(
            db,
            "SECURITY_SOURCE_CONFIGURED",
            actor=user,
            details={
                "source": sources.PROVIDER,
                "enabled": connection.is_enabled,
                "api_key": "set" if connection.secret_encrypted else "none",
                "interval_minutes": interval,
                "created": created,
            },
        )
        db.commit()
    return flash_redirect(request, PAGE, "success", "Configurazione della fonte NVD salvata.", title="Fonte salvata")


def _sync(request: Request, csrf: str, trigger: str):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        connection = sources.connection_for(db)
        if not connection or not connection.is_enabled:
            return flash_redirect(request, PAGE, "warning", "Abilita e salva prima la fonte NVD.", title="Fonte non abilitata")
        result = sources.run_advisory_sync(db, connection, utcnow(), trigger=trigger)
        db.commit()
    if result["status"] != "success":
        return flash_redirect(request, PAGE, "error", f"Aggiornamento NVD non riuscito: {result['error']}", title="Aggiornamento fallito")
    matching = result["matching"]
    message = (
        f"{result['fetched']} advisory letti ({result['created']} nuovi, {result['updated']} aggiornati); "
        f"corrispondenze: {matching['opened'] + matching['reopened']} aperte, {matching['resolved']} risolte."
    )
    if result["parse_errors"]:
        message += f" {result['parse_errors']} record non interpretabili: vedi dettaglio sotto."
    return flash_redirect(
        request,
        PAGE,
        "warning" if result["parse_errors"] else "success",
        message,
        title="Advisory aggiornati",
    )


@router.post(PAGE + "/sync", name="admin_nvd_sync")
def admin_nvd_sync(request: Request, csrf: str = Form(...)):
    return _sync(request, csrf, "manual")


@router.post(PAGE + "/full-sync", name="admin_nvd_full_sync")
def admin_nvd_full_sync(request: Request, csrf: str = Form(...)):
    return _sync(request, csrf, "full")


@router.post(PAGE + "/match", name="admin_nvd_match")
def admin_nvd_match(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        core.require_admin(request, db)
        connection = sources.connection_for(db)
        if not connection:
            return flash_redirect(request, PAGE, "warning", "Configura prima la fonte NVD.", title="Fonte non configurata")
        stats = sources.run_matching(db, connection, utcnow(), trigger="manual")
        db.commit()
    return flash_redirect(
        request,
        PAGE,
        "success",
        f"Corrispondenze ricalcolate: {stats['opened'] + stats['reopened']} aperte, {stats['resolved']} risolte, "
        f"{stats['unknown_assessments']} valutazioni non possibili.",
        title="Corrispondenze aggiornate",
    )


def install_advisory_admin(app) -> None:
    app.include_router(router)
