"""UI/fleet integration for controlled MikroTik agent updates."""
from __future__ import annotations

from app.mikrotik_agent_update import agent_update_status


def install_mikrotik_agent_update_ui(templates):
    # Expose a read-only helper for future/detail templates without copying update
    # policy logic into Jinja.
    templates.env.globals["mikrotik_agent_update_status"] = agent_update_status

    # Agent Fleet already renders row.reasons for any attention state.  Extend
    # its row builder instead of creating another competing fleet route.
    from app import agent_fleet

    previous = agent_fleet._fleet_row
    if getattr(previous, "_nsm_agent_update_wrapped", False):
        return

    def fleet_row_with_update(device, credential, enrollments, pending_count, now):
        row = previous(device, credential, enrollments, pending_count, now)
        update = agent_update_status(device)
        row["agent_update"] = update
        if update["outdated"]:
            if update["source_drift"]:
                reason = "Agent source drift: reinstall/update controllato richiesto"
            elif update["requires_reinstall"]:
                reason = (
                    f"Agent {update['current_version'] or 'non rilevato'}; target {update['target_version']} · "
                    "reinstallazione iniziale richiesta"
                )
            else:
                reason = f"Agent {update['current_version'] or 'non rilevato'}; target {update['target_version']}"
            if reason not in row["reasons"]:
                row["reasons"].append(reason)
            row["attention"] = True
            if row["health"] == "healthy":
                row["health"] = "agent_outdated"
        return row

    fleet_row_with_update._nsm_agent_update_wrapped = True
    agent_fleet._fleet_row = fleet_row_with_update
