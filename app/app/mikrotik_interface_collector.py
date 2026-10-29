"""Interface counters for the traffic graphs: every interface, one failure never hides the others (Agent 0.49.22).

The first collector (Agent 0.49.9) read two groups of *running, static*
interfaces inside a single ``:do on-error`` per group: one interface whose
counters could not be read stopped the whole group, PPP sessions (dynamic
interfaces) were never reported, and a port without link disappeared.  On some
routers only the bridges reached NSM, so they were the only interfaces that
could be monitored.

This collector reads, each interface in its own ``:do on-error``:

1. WAN clients (pppoe-out, LTE, l2tp/sstp/ovpn/pptp clients);
2. Ethernet ports (also without link);
3. every other static interface (VLAN, bridge, wireless, tunnels, bonding…);
4. running dynamic interfaces (PPP server sessions such as ``<pppoe-user>``),

in this order, so the most useful ones fit the size cap.

Agent 0.49.26: no group filters on ``dynamic`` any more.  RouterOS sets that
field only on some interfaces (bridges, PPP sessions): Ethernet ports, VLANs,
PPPoE clients and loopback have no ``dynamic`` at all, so ``dynamic=no``
matched only the bridges, the only interfaces that reached NSM (seen on a hEX
with RouterOS 7.24.4, whose ``/interface`` rows carry ``dynamic`` only on the
two bridges).  The groups are now chosen by type: PPP server sessions by their
``-in`` type, loopback left out.
"""
from __future__ import annotations

import re

from app import mikrotik_agent as agent
from app import mikrotik_legacy as legacy

WAN = ("pppoe-out", "lte", "l2tp-out", "sstp-out", "ovpn-out", "pptp-out")
SESSIONS = ("pppoe-in", "l2tp-in", "sstp-in", "ovpn-in", "pptp-in")
SKIPPED = ("loopback",)
CAPS = {1200: 2400, 6000: 12000}  # legacy header / modern JSON field
_OLD = re.compile(
    r':do \{ :foreach nsmIf in=\[/interface find where running=yes && dynamic=no && type!="ether" && type!="vlan" && type!="bridge"\] do=\{ '
    r':if \(\[:len \$nsmIfaces\] < (?P<cap>\d+)\) do=\{ .*?\n'
    r':do \{ :foreach nsmIf in=\[/interface find where running=yes && dynamic=no && \(type="ether" \|\| type="vlan" \|\| type="bridge"\)\] do=\{ .*?\n'
)


def collector(cap: int) -> str:
    wan = " || ".join(f'type="{kind}"' for kind in WAN)
    sessions = " || ".join(f'type="{kind}"' for kind in SESSIONS)
    other = " && ".join(f'type!="{kind}"' for kind in (*WAN, "ether", *SESSIONS, *SKIPPED))
    entry = '[/interface get $nsmIf name] . "|" . [/interface get $nsmIf type] . "|" . [/interface get $nsmIf rx-byte] . "|" . [/interface get $nsmIf tx-byte] . ";"'
    groups = (f"({wan})", 'type="ether"', other, f"running=yes && ({sessions})")
    return "".join(
        f':do {{ :foreach nsmIf in=[/interface find where {where}] do={{ :do {{ :if ([:len $nsmIfaces] < {cap}) do={{ '
        f':set nsmIfaces ($nsmIfaces . {entry}) }} }} on-error={{}} }} }} on-error={{}}\n'
        for where in groups
    )


def rewrite(source: str) -> str:
    match = _OLD.search(source)
    if not match:
        raise RuntimeError("MikroTik interface collector extension point not found")
    cap = CAPS.get(int(match.group("cap")), int(match.group("cap")))
    return source[:match.start()] + collector(cap) + source[match.end():]


def install_mikrotik_interface_collector() -> None:
    for module, name in ((agent, "_agent_source"), (legacy, "_legacy_agent_source")):
        previous = getattr(module, name)
        if getattr(previous, "_nsm_iface_collector", False):
            continue

        def source(base_url, device_id, raw_secret, check_certificate, _previous=previous):
            return rewrite(_previous(base_url, device_id, raw_secret, check_certificate))

        source._nsm_iface_collector = True
        setattr(module, name, source)
