"""MikroTik agent generation policy.

Core and device-agent versions intentionally evolve independently. Core 0.49
introduced the authenticated source-v1 self-update protocol. Agent 0.49.2 is a
maintenance generation that adds the unified configuration/PPP-tunnel snapshot
improvements while remaining self-updatable from an installed modern 0.49.0+
agent. Agent 0.49.3 adds bounded structured snapshots to the RouterOS 7.12
legacy agent (legacy agents are reinstalled, modern agents self-update).
Agent 0.49.4 rewrites the modern backup uploader (step-level errors, settled
file, flash/ path, progress guard, cleanup) and installs the ops-v2 profile.
"""
from __future__ import annotations

from app import mikrotik_agent_update as updater
from app import mikrotik_agent_update_ui as update_ui

TARGET_AGENT_VERSION = "0.49.8"
SELF_UPDATE_MIN_VERSION = "0.49.0"


def _install_status_policy() -> None:
    previous = updater.agent_update_status
    if getattr(previous, "_nsm_generation_policy", False):
        return

    def generation_status(device):
        status = previous(device)
        data = dict(device.inventory_data or {})
        transport = str(data.get("agent_transport") or "unknown").strip().lower()
        current_base = updater._base_version(data.get("agent_version"))
        capable = bool(
            transport == "modern"
            and current_base
            and updater._version_tuple(current_base) >= updater._version_tuple(SELF_UPDATE_MIN_VERSION)
        )
        status["self_update_capable"] = capable
        status["requires_reinstall"] = bool(status["outdated"] and not capable)
        status["protocol"] = updater.UPDATE_PROTOCOL if capable else "reinstall-only"
        status["self_update_min_version"] = SELF_UPDATE_MIN_VERSION
        return status

    generation_status._nsm_generation_policy = True
    updater.agent_update_status = generation_status
    # mikrotik_agent_update_ui imported the helper by name at module import
    # time, so keep its alias synchronized before its installer runs.
    update_ui.agent_update_status = generation_status


def install_mikrotik_agent_generation() -> None:
    updater.TARGET_AGENT_VERSION = TARGET_AGENT_VERSION
    updater.SELF_UPDATE_MIN_VERSION = SELF_UPDATE_MIN_VERSION
    _install_status_policy()
