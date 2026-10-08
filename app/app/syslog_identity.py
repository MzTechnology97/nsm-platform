"""Who sent this syslog line? (LOG-01 hardening, decided with the operator 2026-10-08).

Rules, in order:

1. **Allowed networks.** A packet whose source is outside the networks the
   admin allowed is rejected before parsing.  With no network configured, only
   addresses currently associated with a device are allowed.
2. **Syslog key.** MikroTik agents (0.49.13+) set ``NSM-<16 hex>`` as logging
   prefix.  A line carrying a known key belongs to that device whatever its
   source address (WAN change, shared NAT, several routers at one customer).
   An unknown key is rejected.
3. **Strict mode.** A device whose router uses its key never gets lines by
   address: a line from its address without the key is rejected (spoofing).
4. **Address, only when certain.** Without key, the source address must lead
   to exactly one device, using only fresh associations:
   - addresses of agent-managed devices count only while the agent sends
     heartbeats (``HEARTBEAT_FRESH``): an address returned to the ISP pool
     stops pointing at our router;
   - manual/UISP/ACS management IPs and addresses assigned by the admin;
   - LAN addresses only when unique.
   When several devices share the address — or a strict router uses it too —
   the syslog hostname must match exactly one of the keyless devices
   (identity, name or the *expected hostname*).
5. Anything else is **discarded and not stored**; only counters remain.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import Device, utcnow

KEY_PREFIX = "NSM-"
KEY_RE = re.compile(r"NSM-([0-9a-f]{16})\b:?\s*")
HEARTBEAT_FRESH = timedelta(minutes=30)
REJECT_REASONS = {
    "network": "rete non consentita",
    "rate": "limite di frequenza",
    "unknown_key": "chiave syslog sconosciuta",
    "strict": "senza chiave da un router in modalità rigorosa",
    "ambiguous": "IP condiviso da più apparati, hostname non risolutivo",
    "unknown": "mittente non associato a un apparato",
    "quota": "quota giornaliera del dispositivo superata",
}


def new_key() -> str:
    return secrets.token_hex(8)


def device_key(device) -> str:
    """The device syslog key, created on first use (caller commits)."""
    data = dict(device.inventory_data or {})
    key = data.get("syslog_key")
    if not (isinstance(key, str) and re.fullmatch(r"[0-9a-f]{16}", key)):
        key = new_key()
        data["syslog_key"] = key
        device.inventory_data = data
    return key


def prefix_for(device) -> str:
    return f"{KEY_PREFIX}{device_key(device)}"


def extract_key(text: str) -> tuple[str | None, str]:
    """(key, text without the key) — the prefix may appear before or after the RouterOS topics."""
    match = KEY_RE.search(text[:400])
    if not match:
        return None, text
    return match.group(1), text[:match.start()] + text[match.end():]


def _norm(value) -> str | None:
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError:
        return None


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed


def parse_networks(values) -> list:
    networks = []
    for value in values or []:
        try:
            networks.append(ipaddress.ip_network(str(value).strip(), strict=False))
        except ValueError:
            continue
    return networks


def names_of(device) -> set[str]:
    data = device.inventory_data or {}
    return {str(n).strip().lower() for n in (device.device_identity, device.name, device.display_name, data.get("syslog_hostname")) if n}


@dataclass
class Index:
    keys: dict = field(default_factory=dict)          # key -> device_id
    strict: set = field(default_factory=set)          # device ids that only accept keyed lines
    candidates: dict = field(default_factory=dict)    # ip -> [(device_id, names)]
    known_ips: set = field(default_factory=set)       # every address tied to a device (allow-list when no network is configured)
    strict_ips: set = field(default_factory=set)      # addresses only reachable through a key
    strict_shared: set = field(default_factory=set)   # addresses used by at least one strict device
    networks: list = field(default_factory=list)
    devices: int = 0


def build_index(db, settings: dict, now=None) -> Index:
    now = now or utcnow()
    index = Index(networks=parse_networks(settings.get("allowed_networks")))
    strict_mode = settings.get("strict_mode", True)
    weak: dict = {}
    for device in db.scalars(select(Device)):
        index.devices += 1
        data = device.inventory_data or {}
        key = data.get("syslog_key")
        if isinstance(key, str) and len(key) == 16:
            index.keys[key] = device.id
        if strict_mode and data.get("syslog_strict"):
            index.strict.add(device.id)
        heartbeat = _parse_time(data.get("last_heartbeat_at"))
        fresh = heartbeat is not None and now - heartbeat <= HEARTBEAT_FRESH
        agent_owned = data.get("management_ip_origin") == "agent"
        strong = [*(data.get("syslog_ips") or [])]
        if device.management_ip and (not agent_owned or fresh):
            strong.append(device.management_ip)
        if fresh:
            strong.append(data.get("last_source_ip"))
        entry = (device.id, names_of(device))
        for ip in strong:
            norm = _norm(ip)
            if not norm:
                continue
            index.known_ips.add(norm)
            if device.id in index.strict:
                index.strict_shared.add(norm)
                continue
            bucket = index.candidates.setdefault(norm, [])
            if all(e[0] != device.id for e in bucket):
                bucket.append(entry)
        if fresh:
            for row in data.get("ip_addresses") or []:
                norm = _norm((row or {}).get("address")) if isinstance(row, dict) else None
                if norm:
                    index.known_ips.add(norm)
                    if device.id not in index.strict and all(e[0] != device.id for e in weak.get(norm, [])):
                        weak.setdefault(norm, []).append(entry)
    for ip, entries in weak.items():
        if ip not in index.candidates and len(entries) == 1:
            index.candidates[ip] = entries
    # Addresses of strict devices: a keyless line from there is spoofing or a misconfiguration.
    index.strict_ips = {ip for ip in index.known_ips if not index.candidates.get(ip)}
    return index


def allowed(index: Index, source_ip: str) -> bool:
    try:
        address = ipaddress.ip_address(source_ip)
    except ValueError:
        return False
    if index.networks:
        return any(address in network for network in index.networks)
    return source_ip in index.known_ips


def resolve(index: Index, source_ip: str, key: str | None, hostname: str | None):
    """(device_id, None) when certain, else (None, reject reason)."""
    if key:
        device_id = index.keys.get(key)
        return (device_id, None) if device_id else (None, "unknown_key")
    entries = index.candidates.get(source_ip) or []
    if not entries:
        return None, "strict" if source_ip in index.strict_ips else "unknown"
    # Without key, the address alone is enough only when no strict router uses it too:
    # otherwise a keyless line could be the strict router (misconfigured or spoofed).
    if len(entries) == 1 and source_ip not in index.strict_shared:
        return entries[0][0], None
    host = (hostname or "").strip().lower()
    matches = [device_id for device_id, names in entries if host and host in names]
    if len(matches) == 1:
        return matches[0], None
    return None, "ambiguous"
