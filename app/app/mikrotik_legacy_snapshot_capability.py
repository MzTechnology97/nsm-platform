"""Agent-status capability bridge for RouterOS 7.12 legacy snapshots."""
from __future__ import annotations

from app import mikrotik_agent_status as agent_status
from app.mikrotik_legacy_jobs import legacy_agent_supports_snapshots


def install_mikrotik_legacy_snapshot_capability() -> None:
    previous = agent_status._agent_capabilities

    def wrapped(device, credential, backup_capability):
        rows = previous(device, credential, backup_capability)
        inventory = dict(device.inventory_data or {})
        transport = agent_status._transport(inventory)
        authenticated = bool(credential and credential.is_active)
        if transport == "legacy":
            for row in rows:
                if row.get("key") == "snapshots":
                    supported = legacy_agent_supports_snapshots(device)
                    row["available"] = authenticated and supported
                    row["detail"] = (
                        "Allow-list plain-text compatibile RouterOS 7.12.x"
                        if supported
                        else "Reinstalla l'agent legacy (0.49.3+) per abilitare gli snapshot strutturati"
                    )
        return rows

    agent_status._agent_capabilities = wrapped
