"""Least-privilege RouterOS execution profile for modern NSM agents.

The legacy 7.12 transport remains read/test only.  Modern agents need the
additional ftp/write/reboot policies for the already allow-listed backup,
package staging and staged-package activation handlers.  No policy/password/
sensitive/sniff/API permissions are granted.
"""
from __future__ import annotations

from app import mikrotik_legacy as legacy

MODERN_PROFILE = "ops-v1"
LEGACY_PROFILE = "legacy-read-v1"
MODERN_POLICIES = "ftp,reboot,read,write,test"
LEGACY_POLICIES = "read,test"


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
            ':if ([:find $nsmAgentSource "firmware_activate"] != nil) do={ '
            ':set nsmAgentPolicy "ftp,reboot,read,write,test" }\n'
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
