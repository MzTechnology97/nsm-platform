"""MikroTik agent transport, snapshot/diagnostic availability and memory formatting (configuration and diagnostics tabs)."""
from __future__ import annotations

import re

_MEMORY_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?i?b)?\s*$", re.I)
_MEMORY_FACTORS = {
    "": 1, "b": 1, "kb": 1000, "kib": 1024,
    "mb": 1000**2, "mib": 1024**2,
    "gb": 1000**3, "gib": 1024**3,
    "tb": 1000**4, "tib": 1024**4,
}


def _memory_bytes(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    match = _MEMORY_RE.match(str(value))
    if not match:
        return None
    factor = _MEMORY_FACTORS.get((match.group(2) or "").lower())
    return int(float(match.group(1)) * factor) if factor is not None else None


def human_bytes(value) -> str:
    number = _memory_bytes(value)
    if number is None:
        return "—" if value in (None, "") else str(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(number)
    index = 0
    while amount >= 1024 and index < len(units) - 1:
        amount /= 1024.0
        index += 1
    return f"{int(amount)} {units[index]}" if index == 0 else f"{amount:.1f} {units[index]}"


def agent_transport(device) -> str:
    inventory = dict(device.inventory_data or {})
    explicit = str(inventory.get("agent_transport") or "").strip().lower()
    if explicit in {"legacy", "modern"}:
        return explicit
    version = str(inventory.get("agent_version") or "").strip().lower()
    if inventory.get("legacy_agent") or version.endswith("-legacy"):
        return "legacy"
    if version:
        return "modern"
    return "unknown"


def _legacy_snapshots(device) -> bool:
    from app.mikrotik_legacy_jobs import legacy_agent_supports_snapshots

    return legacy_agent_supports_snapshots(device)


def snapshot_blocker(device) -> str | None:
    """Why a configuration snapshot cannot be requested from this device's agent, or None."""
    if agent_transport(device) not in {"modern", "legacy"}:
        return "Snapshot configurazione non disponibile: transport agent non rilevato o non validato."
    return None


def diagnostic_blocker(device, diagnostic: str) -> str | None:
    """Why this diagnostic cannot run on the device's agent (support snapshot needs a modern or recent legacy agent), or None."""
    if diagnostic == "support_snapshot":
        transport = agent_transport(device)
        if transport != "modern" and not (transport == "legacy" and _legacy_snapshots(device)):
            return ("Support snapshot non disponibile con questo agent legacy: reinstallalo (0.49.3+). "
                    "Ping, traceroute, neighbor, DHCP lookup e log restano disponibili.")
    return None


