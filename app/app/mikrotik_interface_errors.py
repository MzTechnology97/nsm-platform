"""Interface errors and drops sent by the Agent (MON-01, Agent 0.49.20).

Every heartbeat adds the RouterOS ``rx-error``, ``tx-error``, ``rx-drop`` and
``tx-drop`` counters of the running interfaces that have at least one non-zero
counter, as ``v1;name|rx-error|tx-error|rx-drop|tx-drop;`` (modern:
``metrics.iferrs``; legacy and RouterOS 6: ``X-NSM-Iferr`` header).  The
``v1;`` prefix tells NSM the Agent supports them, so an interface missing from
the list has zero errors.  Interfaces without these properties are skipped.
"""
from __future__ import annotations

from app import mikrotik_agent as agent
from app import mikrotik_legacy as legacy

MODERN_LIMIT = 3000
LEGACY_LIMIT = 500
_COLLECT = (
    ':local nsmIferrs "v1;"\n'
    ':do { :foreach nsmIf in=[/interface find where running=yes && dynamic=no] do={ :do { '
    ':local nsmRe [/interface get $nsmIf rx-error]; :local nsmTe [/interface get $nsmIf tx-error]; '
    ':local nsmRd [/interface get $nsmIf rx-drop]; :local nsmTd [/interface get $nsmIf tx-drop]; '
    ':if ((($nsmRe + $nsmTe + $nsmRd + $nsmTd) > 0) && ([:len $nsmIferrs] < LIMIT)) do={ '
    ':set nsmIferrs ($nsmIferrs . [/interface get $nsmIf name] . "|" . $nsmRe . "|" . $nsmTe . "|" . $nsmRd . "|" . $nsmTd . ";") } '
    '} on-error={} } } on-error={}\n'
)
_MODERN_METRICS = ':local nsmMetrics {"cpu_load"='
_MODERN_FIELD = '"ifaces"=$nsmIfaces;'
_LEGACY_HEADERS = ':local nsmHeaders ("Content-Type:text/plain,X-NSM-Legacy-Transport:headers-v1,'
_LEGACY_FIELD = '",X-NSM-Ifaces:" .'


def collect(limit: int) -> str:
    return _COLLECT.replace("LIMIT", str(limit))


def install_mikrotik_interface_errors() -> None:
    previous_modern = agent._agent_source
    if not getattr(previous_modern, "_nsm_iferrs", False):
        def modern(base_url, device_id, raw_secret, check_certificate):
            source = previous_modern(base_url, device_id, raw_secret, check_certificate)
            if source.count(_MODERN_METRICS) != 1 or _MODERN_FIELD not in source:
                raise RuntimeError("MikroTik interface errors extension point not found (modern)")
            source = source.replace(_MODERN_METRICS, collect(MODERN_LIMIT) + _MODERN_METRICS, 1)
            return source.replace(_MODERN_FIELD, _MODERN_FIELD + '"iferrs"=$nsmIferrs;', 1)

        modern._nsm_iferrs = True
        agent._agent_source = modern

    previous_legacy = legacy._legacy_agent_source
    if not getattr(previous_legacy, "_nsm_iferrs", False):
        def legacy_source(base_url, device_id, raw_secret, check_certificate):
            source = previous_legacy(base_url, device_id, raw_secret, check_certificate)
            if _LEGACY_HEADERS not in source or _LEGACY_FIELD not in source:
                raise RuntimeError("MikroTik interface errors extension point not found (legacy)")
            source = source.replace(_LEGACY_HEADERS, collect(LEGACY_LIMIT) + _LEGACY_HEADERS, 1)
            return source.replace(_LEGACY_FIELD, '",X-NSM-Iferr:" . [$nsmHeaderSafe $nsmIferrs] . ' + _LEGACY_FIELD, 1)

        legacy_source._nsm_iferrs = True
        legacy._legacy_agent_source = legacy_source
