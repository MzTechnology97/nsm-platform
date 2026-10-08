"""Host firewall rules for the syslog ports (LOG-01 hardening).

Docker publishes 514 (and optionally 6514) with its own NAT rules, which bypass
host firewalls such as ufw.  The supported place to filter published ports is
the ``DOCKER-USER`` chain: this module renders an idempotent script that
allows only the networks configured in *Amministrazione → Syslog* and drops
the rest, before the packet ever reaches the receiver.

Usage on the host: ``./manage.sh syslog-firewall`` prints the script,
``./manage.sh syslog-firewall --apply`` runs it (root).  Re-run it after
changing the allowed networks.
"""
from __future__ import annotations

import ipaddress

CHAIN = "NSM-SYSLOG"
# DOCKER-USER sees packets after DNAT: match the container ports.
CONTAINER_PORTS = (("udp", 5514), ("tcp", 5514), ("tcp", 6514))


def render(networks) -> str:
    parsed = []
    for value in networks or []:
        try:
            parsed.append(ipaddress.ip_network(str(value), strict=False))
        except ValueError:
            continue
    if not parsed:
        return ("# Nessuna rete consentita configurata in Amministrazione -> Syslog:\n"
                "# configurale prima di applicare il firewall, altrimenti tutti i log verrebbero bloccati.\n"
                "exit 1\n")
    lines = ["#!/bin/sh", "# NSM syslog: generated from the allowed networks. Idempotent.", "set -e"]
    for tool, version in (("iptables", 4), ("ip6tables", 6)):
        allowed = [n for n in parsed if n.version == version]
        lines.append(f"if command -v {tool} >/dev/null 2>&1; then")
        lines.append(f"  {tool} -N {CHAIN} 2>/dev/null || {tool} -F {CHAIN}")
        for network in allowed:
            lines.append(f"  {tool} -A {CHAIN} -s {network} -j RETURN")
        lines.append(f"  {tool} -A {CHAIN} -j DROP")
        for proto, port in CONTAINER_PORTS:
            rule = f"DOCKER-USER -p {proto} --dport {port} -j {CHAIN}"
            lines.append(f"  {tool} -C {rule} 2>/dev/null || {tool} -I {rule}")
        lines.append("fi")
    return "\n".join(lines) + "\n"


def main() -> None:
    from app.db import SessionLocal
    from app.syslog_receiver import load_settings

    with SessionLocal() as db:
        print(render(load_settings(db).get("allowed_networks")), end="")


if __name__ == "__main__":
    main()
