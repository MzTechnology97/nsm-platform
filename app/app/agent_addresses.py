"""Management IP and addresses of agent-managed MikroTik devices (INV-07).

The agent never had a management IP: the device list showed "—" and the device
page fell back to the heartbeat source address. Agent 0.49.10 reports the
RouterOS addresses (``address/prefix|interface;``); the Core derives:

- ``management_ip``: a public address configured on the router (PPPoE/WAN),
  else the address NSM sees the heartbeat from, else the first address;
- ``lan_ip``: the first private address (bridge/LAN side), shown in the list.

A management IP typed by an operator is never overwritten: only values set by
the agent itself (``management_ip_origin == "agent"``) are refreshed.
"""
from __future__ import annotations

import ipaddress

MAX_ADDRESSES = 32
CGNAT = ipaddress.ip_network("100.64.0.0/10")


def parse_addresses(raw) -> list[dict]:
    rows = []
    for entry in str(raw or "").split(";"):
        address, _, interface = entry.partition("|")
        address = address.strip()
        try:
            iface = ipaddress.ip_interface(address)
        except ValueError:
            continue
        rows.append({"address": str(iface.ip), "prefix": iface.network.prefixlen, "interface": interface.strip()[:100]})
        if len(rows) >= MAX_ADDRESSES:
            break
    return rows


def _ip(value):
    try:
        return ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None


def is_public(value) -> bool:
    ip = _ip(value)
    return bool(ip and ip.is_global and ip not in CGNAT)


def scope_label(value) -> str:
    ip = _ip(value)
    if ip is None:
        return ""
    if is_public(ip):
        return "pubblico"
    if ip in CGNAT:
        return "CGNAT"
    return "privato"


def apply(device, data: dict, raw_addresses, source_ip) -> None:
    """Refresh addresses, LAN IP and (agent-owned) management IP on ``device``/``data``."""
    addresses = parse_addresses(raw_addresses)
    if addresses:
        data["ip_addresses"] = addresses
    addresses = data.get("ip_addresses") if isinstance(data.get("ip_addresses"), list) else []
    public = next((row["address"] for row in addresses if is_public(row.get("address"))), None)
    private = next((row["address"] for row in addresses if not is_public(row.get("address")) and _ip(row.get("address")) and not _ip(row["address"]).is_loopback), None)
    data["lan_ip"] = private
    candidate = public or (source_ip if _ip(source_ip) else None) or (addresses[0]["address"] if addresses else None)
    owned = data.get("management_ip_origin") == "agent"
    if candidate and (not device.management_ip or owned):
        device.management_ip = candidate
        data["management_ip_origin"] = "agent"


def install_agent_addresses() -> None:
    from app import main as core

    core.templates.env.globals.update(ip_scope=scope_label)
