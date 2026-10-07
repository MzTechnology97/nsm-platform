"""Least-privilege RouterOS execution profile for modern NSM agents.

Legacy agents (RouterOS 6.48/6.49 and 7.12) get ftp/reboot/read/write/test from
Agent 0.49.6 for the fixed reboot and package-installer handlers.  Modern agents need the
additional ftp/write/reboot policies for the allow-listed package staging and
staged-package activation handlers.

Profile ops-v2 (Agent 0.49.4) adds ``policy`` and ``sensitive``: RouterOS only
lets a scheduled script run ``/system backup save`` with both, so agents
installed with ops-v1 cannot produce binary backups.  A script cannot raise its
own policies, therefore moving from ops-v1 to ops-v2 needs a reinstall.  No
password/sniff/romon/API permissions are granted.
"""
from __future__ import annotations

from app import mikrotik_legacy as legacy

MODERN_PROFILE = "ops-v2"
# Every profile that can run the reboot/write operational handlers.
OPERATIONAL_PROFILES = frozenset({"ops-v1", "ops-v2"})
# Profiles allowed to run /system backup save from the scheduler.
BACKUP_PROFILES = frozenset({"ops-v2"})
# Legacy agents 0.49.6+ can reboot and run the package update installer.
LEGACY_PROFILE = "legacy-ops-v1"
LEGACY_READ_ONLY_PROFILE = "legacy-read-v1"
MODERN_POLICIES = "ftp,reboot,read,write,policy,test,sensitive"
LEGACY_POLICIES = "ftp,reboot,read,write,test"


def install_mikrotik_privilege_profile():
    previous_bootstrap = legacy._legacy_bootstrap_script
    previous_record_transport = legacy._record_transport

    def bootstrap_with_profile(base_url: str, token: str):
        source = previous_bootstrap(base_url, token)
        script_line = (
            '/system script add name="nsm-agent-heartbeat" policy=read,test '
            'source=$nsmAgentSource comment="NSM managed agent '
        )
        scheduler_line = (
            '/system scheduler add name="nsm-agent-heartbeat" interval=5m '
            'on-event="/system script run nsm-agent-heartbeat" policy=read,test '
            'comment="NSM managed agent"'
        )
        if script_line not in source or scheduler_line not in source:
            raise RuntimeError("MikroTik bootstrap privilege extension point not found")

        dynamic_policy = (
            ':local nsmAgentPolicy "read,test"\n'
            ':if ([:find $nsmAgentSource "legacy_firmware_upgrade"] != nil) do={ '
            ':set nsmAgentPolicy "' + LEGACY_POLICIES + '" }\n'
            ':if ([:find $nsmAgentSource "firmware_activate"] != nil) do={ '
            ':set nsmAgentPolicy "' + MODERN_POLICIES + '" }\n'
        )
        source = source.replace(
            script_line,
            dynamic_policy
            + '/system script add name="nsm-agent-heartbeat" policy=$nsmAgentPolicy '
            + 'source=$nsmAgentSource comment="NSM managed agent ',
            1,
        )
        source = source.replace(
            scheduler_line,
            '/system scheduler add name="nsm-agent-heartbeat" interval=5m '
            'on-event="/system script run nsm-agent-heartbeat" policy=$nsmAgentPolicy '
            'comment="NSM managed agent"',
            1,
        )
        return source

    def record_transport_with_profile(db, device, observed_version, transport, agent_version):
        previous_record_transport(db, device, observed_version, transport, agent_version)
        profile = MODERN_PROFILE if transport == "modern" else LEGACY_PROFILE
        data = dict(device.inventory_data or {})
        data["agent_privilege_profile"] = profile
        device.inventory_data = data
        legacy.agent.core.add_event(
            db,
            "MIKROTIK_AGENT_PRIVILEGE_PROFILE_SELECTED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={
                "transport": transport,
                "profile": profile,
                "policies": MODERN_POLICIES if transport == "modern" else LEGACY_POLICIES,
            },
            source="mikrotik_enrollment",
        )

    legacy._legacy_bootstrap_script = bootstrap_with_profile
    legacy._record_transport = record_transport_with_profile
    return bootstrap_with_profile
