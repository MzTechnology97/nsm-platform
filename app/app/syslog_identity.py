"""Who sent this syslog line? (LOG-01 hardening, decided with the operator 2026-10-08).

Rules, in order:

1. **Allowed networks.** A packet whose source is outside the networks the
   admin allowed is rejected before parsing.  With no network configured, only
   addresses currently associated with a device are allowed.
2. **Syslog key — mandatory (operator decision 2026-10-09).** Every line must
   carry the device key ``NSM-<16 hex>`` (MikroTik logging prefix, Cisco
   origin-id, device name for other vendors); lines without a key are always
   discarded.  Rules 3–4 below describe the former address attribution, kept
   only for diagnostics.  MikroTik agents (0.49.13+) set the key as logging
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
import unicodedata
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
    "missing_key": "riga senza chiave NSM",
    "strict": "senza chiave da un router in modalità rigorosa",
    "ambiguous": "IP condiviso da più apparati, hostname non risolutivo",
    "unknown": "mittente non associato a un apparato",
    "quota": "quota giornaliera del dispositivo superata",
    "decommissioned": "dispositivo disattivato",
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
    retired: set = field(default_factory=set)         # keys of decommissioned devices: their lines are dropped
    by_address: dict = field(default_factory=dict)    # ip -> device_id: TR-069 CPEs NSM pointed at itself (no key possible)
    by_name: list = field(default_factory=list)       # (customer name words, device_id): Ubiquiti set through UISP in customer-name mode
    devices: int = 0


TR069_FRESH = timedelta(minutes=30)


def tr069_addresses(device, now) -> set[str]:
    """Addresses a TR-069 CPE sends syslog from: its management IP and, while its last Inform is recent, the WAN address
    of that Inform (dynamic IPs change at every PPP reconnection; the ACS reads it again at each contact)."""
    found = {_norm(device.management_ip)}
    monitor = ((device.inventory_data or {}).get("tr069") or {}).get("monitor") or {}
    last_inform = _parse_time(monitor.get("last_inform"))
    if last_inform is not None and now - last_inform <= TR069_FRESH:
        found |= {_norm(monitor.get("wan_address")), _norm((monitor.get("wan") or {}).get("ip"))}
    return {ip for ip in found if ip}


def name_tokens(value) -> frozenset:
    """Words of a name without case, accents or punctuation: "Rizzo, Michela" = "MICHELA RIZZO"."""
    folded = unicodedata.normalize("NFD", str(value or "")).encode("ascii", "ignore").decode().upper()
    return frozenset(word for word in re.split(r"[^A-Z0-9]+", folded) if len(word) >= 2)


def build_index(db, settings: dict, now=None) -> Index:
    now = now or utcnow()
    index = Index(networks=parse_networks(settings.get("allowed_networks")))
    strict_mode = settings.get("strict_mode", True)
    weak: dict = {}
    keyless: dict = {}
    by_name: dict = {}
    for device in db.scalars(select(Device).execution_options(include_decommissioned=True)):
        data = device.inventory_data or {}
        key = data.get("syslog_key")
        if device.decommissioned_at is not None:
            if isinstance(key, str) and len(key) == 16:
                index.retired.add(key)
            continue
        index.devices += 1
        if isinstance(key, str) and len(key) == 16:
            index.keys[key] = device.id
        if strict_mode and (data.get("syslog_strict") or (data.get("syslog_strict_manual") and isinstance(key, str) and len(key) == 16)):
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
        if device.management_source == "tr069" and ((data.get("tr069") or {}).get("syslog") or {}).get("server_ip"):
            for ip in tr069_addresses(device, now):
                keyless.setdefault(ip, set()).add(device.id)
                index.known_ips.add(ip)
        uisp_syslog = data.get("uisp_syslog") if isinstance(data.get("uisp_syslog"), dict) else {}
        if device.vendor == "ubiquiti" and uisp_syslog.get("status") == "applied":
            if uisp_syslog.get("mode") == "address" and _norm(device.management_ip):
                keyless.setdefault(_norm(device.management_ip), set()).add(device.id)
                index.known_ips.add(_norm(device.management_ip))
            elif uisp_syslog.get("mode") == "customer_name" and name_tokens(uisp_syslog.get("match_name")):
                # The customer's name saved when NSM configured the device: the receiver's account cannot read customers.
                by_name.setdefault(name_tokens(uisp_syslog.get("match_name")), set()).add(device.id)
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
    # TR-069 CPEs cannot add a key (TP-Link): their address identifies them, only when no other device uses it.
    for ip, ids in keyless.items():
        device_id = next(iter(ids))
        if len(ids) == 1 and ip not in index.strict_shared and all(e[0] == device_id for e in index.candidates.get(ip) or []):
            index.by_address[ip] = device_id
    # Hostname = customer name: only a name that exactly one device uses identifies it.
    index.by_name = [(tokens, next(iter(ids))) for tokens, ids in by_name.items() if len(ids) == 1]
    return index


def allowed(index: Index, source_ip: str) -> bool:
    try:
        address = ipaddress.ip_address(source_ip)
    except ValueError:
        return False
    if index.networks:
        return any(address in network for network in index.networks)
    return source_ip in index.known_ips


def resolve(index: Index, source_ip: str, key: str | None, hostname: str | None, text: str | None = None):
    """(device_id, None) when certain, else (None, reject reason).

    Key only (operator decision 2026-10-09): a line is accepted only when it carries
    the key of a known device; lines without a key are discarded, except from the address
    of a TR-069 CPE that NSM itself set to send syslog (TP-Link takes no key; ``Index.by_address``).
    """
    if key:
        device_id = index.keys.get(key)
        if device_id:
            return device_id, None
        return None, "decommissioned" if key in index.retired else "unknown_key"
    if source_ip in index.by_address:
        return index.by_address[source_ip], None
    if text and index.by_name:
        # airOS puts the hostname (which may contain spaces) before the program name: look at the line's start only.
        words = name_tokens(text[:160])
        matches = {device_id for tokens, device_id in index.by_name if tokens <= words}
        if len(matches) == 1:
            return matches.pop(), None
        if len(matches) > 1:
            return None, "ambiguous"
    return None, "missing_key"


def resolve_by_address(index: Index, source_ip: str, hostname: str | None):
    """Address/hostname attribution used before key-only mode; kept for the device page diagnostics."""
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
