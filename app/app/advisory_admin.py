"""Administration of the NVD advisory source (SEC-01)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import advisory_sources as sources
from app import main as core
from app.integration_models import ConnectorIntegration
from app.models import utcnow
from app.secret_vault import encrypt_text

router = APIRouter()


def save_source(db, user, *, enabled: bool, api_key: str, clear_api_key: bool, interval_minutes) -> ConnectorIntegration:
    """Store the NVD source (key encrypted, kept when empty), audited. HTTPException 400 for an interval out of range. Caller commits."""
    try:
        interval = int(interval_minutes or sources.INTERVAL_DEFAULT_MINUTES)
    except (TypeError, ValueError):
        interval = -1
    if not sources.INTERVAL_MIN_MINUTES <= interval <= sources.INTERVAL_MAX_MINUTES:
        raise HTTPException(400, f"L'intervallo deve essere tra {sources.INTERVAL_MIN_MINUTES} e {sources.INTERVAL_MAX_MINUTES} minuti.")
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
    connection.is_enabled = enabled
    key = (api_key or "").strip()
    if key:
        connection.secret_encrypted = encrypt_text(key)
    elif clear_api_key:
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
    return connection


def sync_now(db, trigger: str) -> dict:
    """Run an advisory sync (``manual`` or ``full``); HTTPException 400 when the source is not enabled. Caller commits."""
    connection = sources.connection_for(db)
    if not connection or not connection.is_enabled:
        raise HTTPException(400, "Abilita e salva prima la fonte NVD.")
    return sources.run_advisory_sync(db, connection, utcnow(), trigger=trigger)


def match_now(db) -> dict:
    """Recompute matches with the inventory; HTTPException 400 when the source is not configured. Caller commits."""
    connection = sources.connection_for(db)
    if not connection:
        raise HTTPException(400, "Configura prima la fonte NVD.")
    return sources.run_matching(db, connection, utcnow(), trigger="manual")


def install_advisory_admin(app) -> None:
    app.include_router(router)
