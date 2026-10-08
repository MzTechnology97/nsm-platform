"""2-minute Agent heartbeat (Agent 0.49.18).

New installations get ``interval=2m`` from the bootstrap.  Installed Agents
receive this generation through the automatic update and the source itself
aligns its scheduler at every run (``/system scheduler set``, policy ``write``);
read-only Agents cannot change it and keep their 5 minutes.

Server load: a heartbeat is one small POST (legacy Agents add one job poll), so
300 routers make about 2.5 requests per second.  Storage is kept in check by
the RRA-style consolidation in ``telemetry_rollup``.
"""
from __future__ import annotations

from app import mikrotik_agent as agent
from app import mikrotik_legacy as legacy

INTERVAL = "2m"
INTERVAL_SECONDS = 120
ENFORCE = (
    ':do { :local nsmSched [/system scheduler find where name="nsm-agent-heartbeat"]; '
    ':if ([:len $nsmSched] = 1) do={ :if ([/system scheduler get $nsmSched interval] != [:totime "00:02:00"]) do={ '
    '/system scheduler set $nsmSched interval=[:totime "00:02:00"] } } } on-error={}\n'
)


def install_mikrotik_heartbeat_interval() -> None:
    for module, name in ((agent, "_agent_source"), (legacy, "_legacy_agent_source")):
        previous = getattr(module, name)
        if getattr(previous, "_nsm_interval", False):
            continue

        def source(base_url, device_id, raw_secret, check_certificate, _previous=previous):
            return ENFORCE + _previous(base_url, device_id, raw_secret, check_certificate)

        source._nsm_interval = True
        setattr(module, name, source)
