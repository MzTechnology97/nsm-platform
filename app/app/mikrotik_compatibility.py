"""Centralized RouterOS compatibility and capability resolver.

Core 0.48 makes RouterOS family selection explicit and fail-safe.  The
resolver distinguishes the transport currently observed on the device from the
transport recommended for the observed RouterOS release, so a firmware change
never silently claims that an agent has already migrated.
"""
from __future__ import annotations

import re
from copy import deepcopy

from app import mikrotik_agent as agent
from app import mikrotik_legacy as legacy
from app.models import Device, utcnow

_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)(?:\.(\d+))?")
PROFILE_SCHEMA = 1

LEGACY_CAPABILITIES = {
    "heartbeat": True,
    "telemetry": True,
    "diagnostics": True,
    "firmware_readiness": True,
    "structured_snapshots": False,
    "backup_https": False,
    "firmware_package_staging": False,
}

MODERN_CAPABILITIES = {
    "heartbeat": True,
    "telemetry": True,
    "diagnostics": True,
    "firmware_readiness": True,
    "structured_snapshots": True,
    "backup_https": True,
    "firmware_package_staging": True,
}

UNKNOWN_CAPABILITIES = {
    key: False for key in MODERN_CAPABILITIES
}


def parse_routeros_version(value: str | None) -> tuple[int, int, int] | None:
    match = _VERSION_RE.match(str(value or ""))
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
    )


def resolve_routeros_compatibility(
    version: str | None,
    *,
    installed_transport: str | None = None,
) -> dict:
    parsed = parse_routeros_version(version)
    normalized = ".".join(str(part) for part in parsed) if parsed else None

    # RouterOS 6.48/6.49 runs the legacy transport through a v6-specific
    # source (no v7-only syntax).  Older and unknown/future majors fail closed.
    if parsed and parsed[0] == 6 and parsed[1] >= 48:
        recommended = "legacy"
        family = "routeros-6-legacy"
        validated = True
        reason = "RouterOS 6.48/6.49 uses the header/plain-text legacy transport with the v6 source variant"
        capabilities = deepcopy(LEGACY_CAPABILITIES)
    elif not parsed or parsed[0] != 7:
        recommended = "unknown"
        family = "unvalidated"
        validated = False
        reason = "RouterOS release not present in the validated compatibility matrix"
        capabilities = deepcopy(UNKNOWN_CAPABILITIES)
    elif parsed[1] <= 12:
        recommended = "legacy"
        family = "routeros-7.12-legacy"
        validated = parsed[1] == 12
        reason = (
            "RouterOS 7.12 uses the header/plain-text legacy transport"
            if validated
            else "RouterOS release is older than the validated 7.12 baseline"
        )
        capabilities = deepcopy(LEGACY_CAPABILITIES if validated else UNKNOWN_CAPABILITIES)
    else:
        recommended = "modern"
        family = "routeros-7.13-plus"
        validated = True
        reason = "RouterOS 7.13+ supports the modern NSM transport primitives"
        capabilities = deepcopy(MODERN_CAPABILITIES)

    observed = installed_transport if installed_transport in {"legacy", "modern"} else None
    migration_required = bool(
        validated
        and observed
        and recommended in {"legacy", "modern"}
        and observed != recommended
    )

    # Effective capabilities follow the actually installed transport.  Desired
    # capabilities describe what becomes available after a required migration.
    if not validated:
        effective = deepcopy(UNKNOWN_CAPABILITIES)
    elif observed == "legacy":
        effective = deepcopy(LEGACY_CAPABILITIES)
    elif observed == "modern":
        effective = deepcopy(MODERN_CAPABILITIES)
    else:
        effective = deepcopy(capabilities)

    return {
        "schema": PROFILE_SCHEMA,
        "routeros_version": str(version or "").strip() or None,
        "normalized_version": normalized,
        "family": family,
        "validated": validated,
        "reason": reason,
        "recommended_transport": recommended,
        "installed_transport": observed,
        "migration_required": migration_required,
        "desired_capabilities": capabilities,
        "effective_capabilities": effective,
    }


def persist_compatibility_profile(
    db,
    device: Device,
    version: str | None = None,
    *,
    installed_transport: str | None = None,
    source: str = "mikrotik_compatibility",
    emit_event: bool = True,
) -> dict:
    data = dict(device.inventory_data or {})
    previous = data.get("compatibility_profile")
    observed_transport = installed_transport or data.get("agent_transport")
    profile = resolve_routeros_compatibility(
        version or device.firmware_version,
        installed_transport=observed_transport,
    )
    profile["evaluated_at"] = utcnow().isoformat()
    data["compatibility_profile"] = profile
    data["compatibility_family"] = profile["family"]
    data["compatibility_validated"] = profile["validated"]
    data["compatibility_migration_required"] = profile["migration_required"]
    device.inventory_data = data

    comparable_previous = dict(previous or {})
    comparable_previous.pop("evaluated_at", None)
    comparable_current = dict(profile)
    comparable_current.pop("evaluated_at", None)
    if emit_event and comparable_previous != comparable_current:
        agent.core.add_event(
            db,
            "MIKROTIK_COMPATIBILITY_PROFILE_CHANGED",
            customer_id=device.customer_id,
            device_id=device.id,
            source=source,
            details={
                "before": comparable_previous or None,
                "after": comparable_current,
            },
        )
    return profile


def install_mikrotik_compatibility_resolver():
    """Install the resolver into enrollment and inventory update paths."""
    original_apply_inventory = agent._apply_inventory
    original_record_transport = legacy._record_transport

    # Avoid double wrapping if an app reload imports the installer twice.
    if getattr(original_apply_inventory, "_nsm_compatibility_wrapped", False):
        return

    def apply_inventory_with_compatibility(db, device, inventory, request, source):
        before, after, changes = original_apply_inventory(db, device, inventory, request, source)
        persist_compatibility_profile(
            db,
            device,
            device.firmware_version,
            source=source,
        )
        return before, after, changes

    apply_inventory_with_compatibility._nsm_compatibility_wrapped = True
    agent._apply_inventory = apply_inventory_with_compatibility

    def supports_modern(version):
        profile = resolve_routeros_compatibility(version)
        return profile["validated"] and profile["recommended_transport"] == "modern"

    legacy._supports_modern_agent = supports_modern

    def record_transport_with_compatibility(db, device, observed_version, transport, agent_version):
        original_record_transport(db, device, observed_version, transport, agent_version)
        persist_compatibility_profile(
            db,
            device,
            observed_version,
            installed_transport=transport,
            source="mikrotik_enrollment",
        )

    legacy._record_transport = record_transport_with_compatibility
