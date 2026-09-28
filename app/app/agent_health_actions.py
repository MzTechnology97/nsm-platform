"""Action Center synchronization for MikroTik agent health."""
from __future__ import annotations

from sqlalchemy import select

from app import main as core
from app.agent_health import AGENT_STALE_AFTER, classify_agent_health, expects_agent
from app.agent_models import DeviceAgentCredential
from app.backup_maintenance import _open_issue, _resolve_issue
from app.models import Device, DeviceEnrollment

CATEGORY = "agent_health"
LEGACY_CATEGORY = "integration"
LEGACY_TITLE = "Agent MikroTik non raggiungibile"

ISSUE_DEFINITIONS = {
    "stale": {
        "title": "Heartbeat agent MikroTik stale",
        "severity": "warning",
        "message": "Nessun contatto autenticato dell'agent è stato ricevuto entro la soglia prevista.",
    },
    "offline": {
        "title": "Agent MikroTik offline",
        "severity": "high",
        "message": "L'apparato gestito dall'agent MikroTik risulta offline nonostante una credenziale agent attiva.",
    },
    "no_credential": {
        "title": "Credenziale agent MikroTik assente",
        "severity": "high",
        "message": "L'apparato è configurato per la gestione tramite agent MikroTik ma non dispone di una credenziale agent attiva.",
    },
}


def _audit(db, event_type, device, health, title, extra=None):
    details = {"health": health, "title": title}
    if extra:
        details.update(extra)
    core.add_event(
        db,
        event_type,
        customer_id=device.customer_id,
        device_id=device.id,
        details=details,
        source="scheduler",
    )


def _resolve_named(db, device, category, title, now, health):
    resolved = _resolve_issue(db, device.id, category, title, now)
    if resolved:
        _audit(
            db,
            "AGENT_HEALTH_ISSUE_RESOLVED",
            device,
            health,
            title,
            {"resolved_count": resolved, "category": category},
        )
    return resolved


def sync_agent_health(db, now):
    """Create/deduplicate/auto-resolve agent-health Action Center issues.

    Pending one-shot enrollment is intentionally not an incident. Manually
    managed MikroTik devices that never opted into the agent are also ignored.
    """
    devices = list(db.scalars(select(Device).where(Device.vendor.ilike("mikrotik"))))
    if not devices:
        return 0

    device_ids = [device.id for device in devices]
    credentials = {
        item.device_id: item
        for item in db.scalars(
            select(DeviceAgentCredential).where(
                DeviceAgentCredential.device_id.in_(device_ids),
                DeviceAgentCredential.agent_type == "mikrotik_agent",
            )
        )
    }
    pending_counts = {}
    pending = list(
        db.scalars(
            select(DeviceEnrollment).where(
                DeviceEnrollment.device_id.in_(device_ids),
                DeviceEnrollment.status == "pending",
                DeviceEnrollment.expires_at > now,
            )
        )
    )
    for enrollment in pending:
        pending_counts[enrollment.device_id] = pending_counts.get(enrollment.device_id, 0) + 1

    changed = 0
    managed_titles = [definition["title"] for definition in ISSUE_DEFINITIONS.values()]

    for device in devices:
        credential = credentials.get(device.id)
        pending_count = pending_counts.get(device.id, 0)
        if not expects_agent(device, credential, pending_count):
            # Close historical generic alerts if a device was later switched to
            # manual management.
            changed += _resolve_named(db, device, LEGACY_CATEGORY, LEGACY_TITLE, now, "manual")
            for title in managed_titles:
                changed += _resolve_named(db, device, CATEGORY, title, now, "manual")
            continue

        row = classify_agent_health(device, credential, pending_count, now)
        health = row["health"]

        # Core 0.36 replaces the old generic integration issue with a typed
        # agent-health issue. Resolve it once during the transition.
        changed += _resolve_named(db, device, LEGACY_CATEGORY, LEGACY_TITLE, now, health)

        active_definition = ISSUE_DEFINITIONS.get(health)
        active_title = active_definition["title"] if active_definition else None

        for title in managed_titles:
            if title != active_title:
                changed += _resolve_named(db, device, CATEGORY, title, now, health)

        if not active_definition:
            continue

        if health == "stale" and device.status == "online":
            # Preserve the historical offline projection used by inventory,
            # while the shared classifier keeps the condition typed as stale
            # until a fresh authenticated contact arrives.
            device.status = "offline"

        reference = row["last_seen"]
        details = {
            "kind": health,
            "message": active_definition["message"],
            "transport": row["transport"],
            "agent_version": row["agent_version"],
            "last_contact": reference.isoformat() if reference else None,
            "threshold_minutes": int(AGENT_STALE_AFTER.total_seconds() / 60),
            "pending_enrollments": row["pending_enrollments"],
            "workspace_url": f"/devices/{device.id}/agent",
        }
        _, created = _open_issue(
            db,
            device,
            CATEGORY,
            active_title,
            details,
            severity=active_definition["severity"],
        )
        if created:
            changed += 1
            _audit(
                db,
                "AGENT_HEALTH_ISSUE_OPENED",
                device,
                health,
                active_title,
                {"severity": active_definition["severity"], "transport": row["transport"]},
            )

    return changed
