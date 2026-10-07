"""Integrations hub: real state of every data source NSM depends on.

Each card reflects the runtime state of an integration (configured, healthy,
last synchronisation) or states explicitly that it is not available yet, so the
page never advertises a connector that does not exist.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app import main as core
from app.agent_models import DeviceAgentCredential
from app.api_key_models import PlatformApiKey
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import Device, SecurityAdvisory, utcnow
from app.uisp_connector import UISP_PROVIDER, _sync_view

router = APIRouter()
STALE_AGENT_AFTER = timedelta(minutes=15)


@router.get("/integrations", response_class=HTMLResponse, name="integrations_hub")
def integrations_hub(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        now = utcnow()
        agents = list(
            db.scalars(
                select(DeviceAgentCredential).where(
                    DeviceAgentCredential.agent_type == "mikrotik_agent",
                    DeviceAgentCredential.is_active.is_(True),
                )
            )
        )
        stale_agents = sum(1 for a in agents if not a.last_used_at or a.last_used_at < now - STALE_AGENT_AFTER)
        uisp = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == UISP_PROVIDER))
        uisp_devices = db.scalar(
            select(func.count(Device.id)).where(Device.vendor == "ubiquiti", Device.external_device_id.is_not(None))
        ) or 0
        api_keys_active = db.scalar(
            select(func.count(PlatformApiKey.id)).where(PlatformApiKey.is_active.is_(True))
        ) or 0
        nvd = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "nvd"))
        nvd_advisories = db.scalar(
            select(func.count(SecurityAdvisory.id)).where(SecurityAdvisory.source == "nvd")
        ) or 0
        return core.render(
            request,
            db,
            user,
            "integrations_hub.html",
            nvd=nvd,
            nvd_sync=dict(((nvd.settings or {}).get("sync") or {}) if nvd else {}),
            nvd_advisories=nvd_advisories,
            agents_total=len(agents),
            agents_stale=stale_agents,
            uisp=uisp,
            uisp_sync=_sync_view(uisp),
            uisp_devices=uisp_devices,
            api_keys_active=api_keys_active,
            can_admin=core.has_permission(user, "users.manage"),
        )


def install_integrations_hub(app) -> None:
    app.include_router(router)
