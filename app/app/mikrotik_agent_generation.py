"""MikroTik agent generation policy.

Core and device-agent versions intentionally evolve independently. Core 0.49
introduced the authenticated source-v1 self-update protocol. Agent 0.49.2 is a
maintenance generation that adds the unified configuration/PPP-tunnel snapshot
improvements while remaining self-updatable from an installed modern 0.49.0+
agent. Agent 0.49.3 adds bounded structured snapshots to the RouterOS 7.12
legacy agent (legacy agents are reinstalled, modern agents self-update).
Agent 0.49.4 rewrites the modern backup uploader (step-level errors, settled
file, flash/ path, progress guard, cleanup) and installs the ops-v2 profile.
Agent 0.49.9 adds the interface byte counters to every heartbeat (traffic graphs).
Agent 0.49.10 adds the RouterOS IP addresses (management/LAN IP in the device list).
Agent 0.49.11 can configure remote syslog toward NSM (job syslog_configure).
Agent 0.49.12 adds the read-only snapshot section 'services' (exposed services, SCAN-01).
Agent 0.49.13 sets the per-device syslog key as logging prefix (syslog strict mode).
Agent 0.49.14 adds /ip firewall raw to the firewall snapshot (exposure check).
Agent 0.49.15: the legacy Agent (ops profile) configures remote syslog with the device key.
Agent 0.49.16: the legacy Agent uploads binary backup and export over FTP (RouterOS 6 / 7.12).
Agent 0.49.17: the legacy Agent updates itself (agent_self_update) and reports its RouterOS policies.
Agent 0.49.18: 2-minute heartbeat; the Agent aligns its own scheduler (write policy).
"""
from __future__ import annotations

from app import mikrotik_agent_update as updater
from app import mikrotik_agent_update_ui as update_ui

TARGET_AGENT_VERSION = "0.49.18"
SELF_UPDATE_MIN_VERSION = "0.49.0"


def profile_reinstall_reason(device) -> str | None:
    """Why the installed RouterOS policies are insufficient, or None."""
    from app.mikrotik_privilege_profile import BACKUP_PROFILES, LEGACY_PROFILE

    data = dict(device.inventory_data or {})
    transport = str(data.get("agent_transport") or "").strip().lower()
    profile = str(data.get("agent_privilege_profile") or "").strip()
    if transport == "modern" and profile not in BACKUP_PROFILES:
        return (
            f"Profilo privilegi {profile or 'non registrato'}: il backup binario richiede il profilo ops-v2 "
            "(policy «policy» e «sensitive»). Usa «Rigenera / reinstalla agent»: il self-update non cambia i permessi."
        )
    if transport == "legacy" and profile != LEGACY_PROFILE:
        return (
            f"Agent legacy con profilo {profile or 'non registrato'} (sola lettura): per riavvio, aggiornamento RouterOS "
            "e backup .rsc reinstalla l'agent (profilo legacy-ops-v1)."
        )
    return None


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
        # A self-update replaces the source but keeps the script policies:
        # agents installed with an older profile must be reinstalled.
        reason = profile_reinstall_reason(device)
        status["reinstall_reason"] = reason
        if reason:
            status["outdated"] = True
            status["requires_reinstall"] = True
            status["protocol"] = "reinstall-only"
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
