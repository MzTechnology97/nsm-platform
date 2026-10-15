"""Integrations hub: real state of every data source NSM depends on.

Each card reflects the runtime state of an integration (configured, healthy,
last synchronisation) or states explicitly that it is not available yet, so the
page never advertises a connector that does not exist.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.agent_models import DeviceAgentCredential
from app.api_key_models import PlatformApiKey
from app.integration_models import ConnectorIntegration
from app.models import Device, SecurityAdvisory, utcnow
from app.uisp_connector import UISP_PROVIDER, _sync_view

STALE_AGENT_AFTER = timedelta(minutes=15)


def hub_state(db) -> dict:
    """Runtime state of every integration, shared by the classic hub and the new interface."""
    now = utcnow()
    agents = list(
        db.scalars(
            select(DeviceAgentCredential).where(
                DeviceAgentCredential.agent_type == "mikrotik_agent",
                DeviceAgentCredential.is_active.is_(True),
            )
        )
    )
    uisp = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == UISP_PROVIDER))
    nvd = db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == "nvd"))
    return {
        "agents_total": len(agents),
        "agents_stale": sum(1 for a in agents if not a.last_used_at or a.last_used_at < now - STALE_AGENT_AFTER),
        "uisp": uisp,
        "uisp_sync": _sync_view(uisp),
        "uisp_devices": db.scalar(
            select(func.count(Device.id)).where(Device.vendor == "ubiquiti", Device.external_device_id.is_not(None))
        ) or 0,
        "api_keys_active": db.scalar(select(func.count(PlatformApiKey.id)).where(PlatformApiKey.is_active.is_(True))) or 0,
        "nvd": nvd,
        "nvd_sync": dict(((nvd.settings or {}).get("sync") or {}) if nvd else {}),
        "nvd_advisories": db.scalar(select(func.count(SecurityAdvisory.id)).where(SecurityAdvisory.source == "nvd")) or 0,
    }


