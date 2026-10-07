"""RouterOS 6.48/6.49 variant of the legacy Agent.

RouterOS checks every command and argument when a script is loaded, so a
single v7-only construct makes the whole Agent unusable on v6.  The legacy
transport (bodyless enrollment, header heartbeat, rows-v1 jobs) already avoids
JSON; this module rewrites the few legacy constructs that v6 does not accept
and refuses to hand out a v6 source that still contains a v7-only construct:

* ``/ping ... as-value`` does not exist on v6: the ping returns the number of
  replies, reported as ``sent=10;received=N``;
* ``/tool traceroute ... as-value`` is not available: the job is refused
  server-side and the Agent raises an explicit error if one arrives anyway;
* ``[menu get $id]`` without a property is not relied upon: snapshot sections
  iterate ``print as-value`` rows (bounded output as for 7.12).
"""
from __future__ import annotations

import re

from app import mikrotik_legacy as legacy

_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)")
MIN_V6 = (6, 48)
TRACEROUTE_UNSUPPORTED = "Traceroute non disponibile dall'agent su RouterOS 6: usa il ping o aggiorna a RouterOS 7."

_PING_RE = re.compile(r"\[:tostr \[/ping (address=[^\]]*?) as-value\]\]")
_TRACEROUTE_RE = re.compile(r":set nsmJobOutput \[:tostr \[/tool traceroute [^\]]*? as-value\]\]")
_FIND_RE = re.compile(r":local nsmIds \[(?P<menu>/[a-z0-9 -]+?) find\]")
_GET_ROW_RE = re.compile(r"\n(?P<indent>[ \t]*):local nsmRow \[/[a-z0-9 -]+? get \$nsmId\]")
_V7_ONLY = (
    (re.compile(r":serialize|:deserialize|:convert "), "JSON/convert"),
    (re.compile(r"/file read"), "/file read"),
    (re.compile(r"/ping [^\]\n]*as-value"), "ping as-value"),
    (re.compile(r"/tool traceroute [^\]\n]*as-value"), "traceroute as-value"),
    (re.compile(r" get \$nsmId\]"), "get without property"),
    (re.compile(r"/interface wifi|/routing/|/ip/"), "v7 menu path"),
    (re.compile(r"[!=]= ?nil\)|= nil\)"), "nil literal"),
)


def is_routeros6(version) -> bool:
    match = _VERSION_RE.match(str(version or ""))
    return bool(match) and int(match.group(1)) == 6 and (int(match.group(1)), int(match.group(2))) >= MIN_V6


def to_routeros6(source: str) -> str:
    source = _PING_RE.sub(r'("sent=10;received=" . [/ping \1])', source)
    source = _TRACEROUTE_RE.sub(':error "NSM traceroute unsupported on RouterOS 6"', source)
    source = _FIND_RE.sub(r":local nsmIds [\g<menu> print as-value]", source)
    source = _GET_ROW_RE.sub("", source)
    return source.replace(":foreach nsmId in=$nsmIds do={", ":foreach nsmRow in=$nsmIds do={")


def validate_routeros6(source: str) -> None:
    for pattern, label in _V7_ONLY:
        match = pattern.search(source)
        if match:
            raise RuntimeError(f"RouterOS 6 agent contains a v7-only construct ({label}): {match.group(0)}")


def install_mikrotik_routeros6() -> None:
    previous = legacy._select_agent_source
    if getattr(previous, "_nsm_routeros6", False):
        return

    def select_with_routeros6(base_url, device_id, raw_secret, observed_version):
        source, transport, agent_version = previous(base_url, device_id, raw_secret, observed_version)
        if transport == "legacy" and is_routeros6(observed_version):
            source = to_routeros6(source)
            validate_routeros6(source)
        return source, transport, agent_version

    select_with_routeros6._nsm_routeros6 = True
    legacy._select_agent_source = select_with_routeros6
