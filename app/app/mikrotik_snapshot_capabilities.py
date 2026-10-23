"""Explicit capability gate of the read-only RouterOS snapshot sections (MTK-05).

An Agent ignores a section it does not know and would report an empty success,
so NSM decides per section and per transport whether the installed Agent can
read it, before queueing the job and again before handing it to a legacy Agent:

* base sections: every modern Agent, legacy Agent 0.49.3+ (rows-v1 transport);
* ``services`` (exposed services, SCAN-01): modern Agent 0.49.12+ only;
* ``queues``, ``certificates``, ``routing`` (ISP diagnostics): Agent 0.49.24+,
  modern and legacy (RouterOS 6.48/6.49 and 7.12).
"""
from __future__ import annotations

from app import mikrotik_agent_update as updater

EXTRA_SECTIONS = ("queues", "certificates", "routing")
EXTRA_MIN_AGENT = "0.49.24"
LEGACY_BASE_MIN_AGENT = "0.49.3"
# section -> {transport: minimum Agent version ("" = any), or None when the transport cannot read it}
REQUIREMENTS = {
    **{section: {"modern": "", "legacy": LEGACY_BASE_MIN_AGENT}
       for section in ("resources", "ip_addresses", "routes", "interfaces", "firewall", "ppp_active", "dhcp_leases", "logs")},
    "services": {"modern": "0.49.12", "legacy": None},
    **{section: {"modern": EXTRA_MIN_AGENT, "legacy": EXTRA_MIN_AGENT} for section in EXTRA_SECTIONS},
}
MESSAGES = {
    "no_transport": "Snapshot non disponibile: transport agent non rilevato o non validato.",
    "transport": "Sezione non disponibile con l'agent legacy (RouterOS 6/7.12).",
    "agent_outdated": "Sezione disponibile dall'agent {version}: aggiorna o reinstalla l'agent dalla scheda Agent.",
}


def _transport(device) -> str:
    from app.mikrotik_workspace_ux import agent_transport

    return agent_transport(device)


def section_support(device, section: str) -> dict:
    """``{"supported", "reason", "min_agent"}`` for one snapshot section on this device's Agent.

    ``reason`` is ``None`` when supported, else ``no_transport``, ``transport`` (the legacy transport cannot read
    it) or ``agent_outdated`` (``min_agent`` says which Agent version is needed).
    """
    requirement = REQUIREMENTS.get(section)
    transport = _transport(device)
    if requirement is None or transport not in requirement:
        return {"supported": False, "reason": "no_transport", "min_agent": None}
    minimum = requirement[transport]
    if minimum is None:
        return {"supported": False, "reason": "transport", "min_agent": None}
    installed = updater._version_tuple((device.inventory_data or {}).get("agent_version"))
    if minimum and (installed is None or installed < updater._version_tuple(minimum)):
        return {"supported": False, "reason": "agent_outdated", "min_agent": minimum}
    return {"supported": True, "reason": None, "min_agent": minimum or None}


def unsupported_message(support: dict) -> str:
    return MESSAGES[support["reason"]].format(version=support.get("min_agent") or "")
