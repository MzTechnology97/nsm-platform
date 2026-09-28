"""Shared MikroTik agent-health classification.

Core 0.36 keeps the Agent Fleet UI and background maintenance on exactly the
same rules so a device cannot be healthy in one surface and stale in another.
"""
from __future__ import annotations

from datetime import timedelta, timezone

AGENT_STALE_AFTER = timedelta(minutes=15)


def aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def transport_for_inventory(inventory: dict) -> str:
    explicit = str(inventory.get("agent_transport") or "").strip().lower()
    if explicit in {"modern", "legacy"}:
        return explicit
    version = str(inventory.get("agent_version") or "").strip().lower()
    if inventory.get("legacy_agent") or version.endswith("-legacy"):
        return "legacy"
    if version:
        return "modern"
    return "unknown"


def contact_reference(device, credential):
    """Return the freshest authenticated agent contact known to the platform."""
    candidates = [aware(getattr(device, "last_seen", None))]
    if credential is not None:
        candidates.extend(
            [
                aware(getattr(credential, "last_used_at", None)),
                aware(getattr(credential, "created_at", None)),
            ]
        )
    return max((value for value in candidates if value is not None), default=None)


def classify_agent_health(device, credential, pending_count: int, now):
    inventory = dict(device.inventory_data or {})
    transport = transport_for_inventory(inventory)
    reference = contact_reference(device, credential)
    credential_active = bool(credential and credential.is_active)
    pending_count = max(0, int(pending_count or 0))
    stale = bool(credential_active and (not reference or now - reference > AGENT_STALE_AFTER))
    offline = device.status != "online"

    if not credential_active and pending_count:
        health = "pending"
        reasons = ["Enrollment agent in attesa"]
    elif not credential_active:
        health = "no_credential"
        reasons = ["Credenziale agent assente o non attiva"]
    elif stale:
        # Heartbeat freshness wins over the persisted device status. The worker
        # may mark the device offline after a stale heartbeat; subsequent ticks
        # must remain a single stale condition rather than oscillating.
        health = "stale"
        reasons = ["Heartbeat oltre 15 minuti"]
    elif offline:
        health = "offline"
        reasons = ["Apparato non online"]
    elif transport == "unknown":
        health = "unknown_transport"
        reasons = ["Transport agent non rilevato"]
    else:
        health = "healthy"
        reasons = []

    return {
        "device": device,
        "transport": transport,
        "agent_version": inventory.get("agent_version"),
        "enrollment_transport": inventory.get("enrollment_transport"),
        "heartbeat_transport": inventory.get("legacy_heartbeat_transport") or ("json-v1" if transport == "modern" else None),
        "source_ip": inventory.get("last_source_ip"),
        "last_seen": reference,
        "stale": stale,
        "credential_active": credential_active,
        "credential_created_at": aware(credential.created_at) if credential else None,
        "credential_last_used_at": aware(credential.last_used_at) if credential else None,
        "pending_enrollments": pending_count,
        "health": health,
        "attention": health != "healthy",
        "reasons": reasons,
    }


def expects_agent(device, credential=None, pending_count: int = 0) -> bool:
    inventory = dict(device.inventory_data or {})
    return bool(
        device.management_source == "mikrotik_agent"
        or credential is not None
        or pending_count
        or inventory.get("agent_version")
        or inventory.get("agent_transport")
    )
