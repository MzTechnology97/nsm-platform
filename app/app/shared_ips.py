"""Devices sharing one address (customer NAT, several routers on one public IP).

A shared management IP means:

- syslog: the address alone does not identify the device (see syslog_identity);
- Zabbix: ping/SNMP toward that address reach only the edge router, so a
  device behind it needs its own reachable address (LAN via VPN/proxy);
- exposure: what is reachable from the Internet on that address is the edge
  router; devices behind it are exposed only through port forwards
  (``port_forwards`` below, read from the MikroTik NAT rules).
"""
from __future__ import annotations

import ipaddress

from sqlalchemy import select

from app.models import Device

MANAGEMENT_PORTS = {21: "FTP", 22: "SSH", 23: "Telnet", 80: "HTTP", 161: "SNMP", 443: "HTTPS", 445: "SMB", 3389: "RDP",
                    7547: "TR-069", 8080: "HTTP alt", 8291: "Winbox", 8443: "HTTPS alt", 8728: "API RouterOS", 8729: "API-SSL"}


def _norm(value) -> str | None:
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError:
        return None


def shared_map(db) -> dict:
    """{ip: [devices]} for management IPs used by more than one device."""
    groups: dict = {}
    for device in db.scalars(select(Device).where(Device.management_ip.is_not(None))):
        ip = _norm(device.management_ip)
        if ip:
            groups.setdefault(ip, []).append(device)
    return {ip: devices for ip, devices in groups.items() if len(devices) > 1}


def peers(db, device) -> list:
    ip = _norm(device.management_ip)
    if not ip:
        return []
    return [d for d in db.scalars(select(Device).where(Device.management_ip == device.management_ip, Device.id != device.id))]


def device_addresses(device) -> set[str]:
    data = device.inventory_data or {}
    found = {_norm(device.management_ip), _norm(data.get("lan_ip"))}
    found |= {_norm((row or {}).get("address")) for row in data.get("ip_addresses") or [] if isinstance(row, dict)}
    return {ip for ip in found if ip}


def _ports(value) -> list[int]:
    ports = []
    for part in str(value or "").split(","):
        part = part.strip()
        if "-" in part:
            low, _, high = part.partition("-")
            if low.isdigit() and high.isdigit() and int(high) - int(low) <= 1024:
                ports.extend(range(int(low), int(high) + 1))
        elif part.isdigit():
            ports.append(int(part))
    return ports


def port_forwards(nat_rules: list, wan_check, raw_blocks=None) -> list[dict]:
    """Destination NAT rules that publish internal addresses on the WAN.

    ``wan_check(rule)`` returns 'yes'/'no'/'unknown' for the in-interface
    conditions (same logic as the input-chain evaluation).
    """
    forwards = []
    for number, rule in enumerate(nat_rules or []):
        if str(rule.get("chain")) != "dstnat" or str(rule.get("action")) not in ("dst-nat", "netmap"):
            continue
        if str(rule.get("disabled")).lower() in ("true", "yes"):
            continue
        target = _norm(str(rule.get("to-addresses") or "").split("-")[0])
        if not target:
            continue
        applies = wan_check(rule)
        if applies == "no":
            continue
        public_ports = _ports(rule.get("dst-port"))
        target_ports = _ports(rule.get("to-ports")) or public_ports
        sensitive = sorted({MANAGEMENT_PORTS[p] for p in (target_ports or []) if p in MANAGEMENT_PORTS})
        protocols = [rule.get("protocol")] if rule.get("protocol") else ["tcp", "udp"]
        blocked = bool(raw_blocks and public_ports and all(raw_blocks(p, proto) for p in public_ports for proto in protocols))
        forwards.append({
            "rule": number, "comment": rule.get("comment") or "", "protocol": rule.get("protocol") or "any",
            "public_ports": rule.get("dst-port") or "tutte", "to_address": target, "to_ports": rule.get("to-ports") or rule.get("dst-port") or "tutte",
            "all_ports": not public_ports, "certain": applies == "yes" and not blocked, "sensitive": sensitive, "blocked_by_raw": blocked,
            "severity": "info" if blocked else ("high" if sensitive or not public_ports else "low"),
        })
    return forwards


def forwards_to(db, device) -> list[dict]:
    """Port forwards on routers of the same customer that point at this device."""
    addresses = device_addresses(device)
    if not addresses:
        return []
    found = []
    for router in db.scalars(select(Device).where(Device.customer_id == device.customer_id, Device.id != device.id, Device.vendor == "mikrotik")):
        exposure = (router.inventory_data or {}).get("exposure") or {}
        for forward in exposure.get("port_forwards") or []:
            if forward.get("to_address") in addresses and not forward.get("blocked_by_raw"):
                found.append({**forward, "router_id": str(router.id), "router": router.display_name or router.device_identity or router.name})
    return found
