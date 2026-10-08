"""Historical telemetry bridge for RouterOS 7.12 legacy agents.

The headers-v1 legacy heartbeat already carries CPU, memory and uptime through
sanitized HTTP headers. This module persists those observations into the same
DeviceMetricSample store used by the modern agent, without adding JSON,
:serialize/:deserialize, or a new RouterOS command channel.
"""
from __future__ import annotations

from fastapi import Request

from app import agent_addresses, interface_traffic
from app import mikrotik_agent as agent
from app import mikrotik_agent_status as agent_status
from app import mikrotik_legacy as legacy
from app import mikrotik_telemetry as telemetry
from app import mikrotik_workspace as workspace
from app import mikrotik_workspace_ux as workspace_ux
from app.db import SessionLocal


def _remove_legacy_heartbeat_route(router) -> None:
    router.routes[:] = [
        route
        for route in router.routes
        if not (
            getattr(route, "path", None) == "/api/v1/agents/mikrotik/heartbeat-legacy"
            and "POST" in (getattr(route, "methods", set()) or set())
        )
    ]


async def legacy_heartbeat_with_history(request: Request):
    """Run the proven legacy heartbeat, then persist one metric sample."""
    response = await legacy.mikrotik_heartbeat_legacy(request)
    header_mode = request.headers.get("X-NSM-Legacy-Transport", "").lower() == "headers-v1"
    if not header_mode:
        return response

    inventory, metrics = legacy._header_inventory(request)
    sample = None
    with SessionLocal() as db:
        device, _credential = agent._authenticate_agent(db, request)
        sample = telemetry._metric_sample(device, inventory, metrics)
        if sample is not None:
            sample.source = "mikrotik_agent_legacy"
            db.add(sample)
        data = dict(device.inventory_data or {})
        ifaces = request.headers.get("X-NSM-Ifaces")
        if ifaces:
            interface_traffic.record(db, device, data, ifaces, errors=request.headers.get("X-NSM-Iferr"))
        agent_addresses.apply(device, data, request.headers.get("X-NSM-Addrs"), data.get("last_source_ip"))
        device.inventory_data = data
        db.commit()
    if isinstance(response, dict):
        response = {**response, "telemetry_sampled": sample is not None}
    return response


def _legacy_history_context(previous):
    def wrapped(db, device):
        ctx = previous(db, device)
        if ctx.get("agent_transport") == "legacy":
            ctx["telemetry_history_supported"] = True
        return ctx

    return wrapped


def _legacy_history_capabilities(previous):
    def wrapped(device, credential, backup_capability):
        rows = previous(device, credential, backup_capability)
        inventory = dict(device.inventory_data or {})
        transport = agent_status._transport(inventory)
        authenticated = bool(credential and credential.is_active)
        if transport == "legacy":
            for row in rows:
                if row.get("key") == "telemetry":
                    row["available"] = authenticated
                    row["detail"] = "Retention 90 giorni · metriche heartbeat legacy"
        return rows

    return wrapped


def install_mikrotik_legacy_telemetry() -> None:
    """Patch the legacy APIRouter before it is mounted into the application."""
    _remove_legacy_heartbeat_route(legacy.router)
    legacy.router.add_api_route(
        "/api/v1/agents/mikrotik/heartbeat-legacy",
        legacy_heartbeat_with_history,
        methods=["POST"],
        name="mikrotik_heartbeat_legacy",
    )

    enhanced = _legacy_history_context(workspace_ux._enhanced_context)
    workspace_ux._enhanced_context = enhanced
    workspace._workspace_context = enhanced
    telemetry._workspace_context = enhanced
    agent_status._workspace_context = enhanced
    agent_status._agent_capabilities = _legacy_history_capabilities(agent_status._agent_capabilities)
