"""Host firewall rules for the legacy backup FTP receiver.

Docker publishes the control port and the passive ports with its own NAT rules, which bypass host firewalls such as
ufw: the place to filter them is the ``DOCKER-USER`` chain (packets there already carry the container ports). This
renders an idempotent script that lets only the subnets allowed in *Amministrazione → Sistema* reach the receiver.
The receiver refuses other origins by itself as well; these rules stop them before they reach it.

On the host: ``./manage.sh legacy-ftp-firewall`` prints the script, ``--apply`` runs it (root);
``./manage.sh legacy-backup-enable`` applies it when subnets are configured.
"""
from __future__ import annotations

import os

from app.legacy_ftp_backup import parse_networks

CHAIN = "NSM-LEGACY-FTP"
CONTROL_PORT = 2121  # inside the container (LEGACY_FTP_LISTEN_PORT); the host port is mapped onto it


def render(networks, passive: str | None = None) -> str:
    parsed = parse_networks(networks)
    if not parsed:
        return ("# Nessuna subnet consentita configurata in Amministrazione -> Sistema -> Backup legacy via FTP:\n"
                "# il ricevitore accetta qualsiasi origine. Configura le subnet prima di applicare il firewall.\n"
                "exit 1\n")
    low, _, high = (passive or os.getenv("LEGACY_FTP_PASV_PORTS", "30100-30109")).partition("-")
    ports = f"{int(low)}:{int(high or low)}"
    lines = ["#!/bin/sh", "# NSM legacy backup FTP: generated from the allowed subnets. Idempotent.", "set -e"]
    for tool, version in (("iptables", 4), ("ip6tables", 6)):
        allowed = [n for n in parsed if n.version == version]
        lines.append(f"if command -v {tool} >/dev/null 2>&1; then")
        lines.append(f"  {tool} -N {CHAIN} 2>/dev/null || {tool} -F {CHAIN}")
        for network in allowed:
            lines.append(f"  {tool} -A {CHAIN} -s {network} -j RETURN")
        lines.append(f"  {tool} -A {CHAIN} -j DROP")
        for spec in (f"--dport {CONTROL_PORT}", f"-m multiport --dports {ports}"):
            rule = f"DOCKER-USER -p tcp {spec} -j {CHAIN}"
            lines.append(f"  {tool} -C {rule} 2>/dev/null || {tool} -I {rule}")
        lines.append("fi")
    return "\n".join(lines) + "\n"


def main() -> None:
    from app.db import SessionLocal
    from app.legacy_ftp_backup import allowed_networks

    with SessionLocal() as db:
        print(render(allowed_networks(db)), end="")


if __name__ == "__main__":
    main()
