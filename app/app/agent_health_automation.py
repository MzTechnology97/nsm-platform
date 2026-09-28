"""Automated Action Center and recovery notifications for MikroTik agent health."""
from __future__ import annotations

from datetime import timedelta, timezone

from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.mikrotik_agent_status import HEARTBEAT_STALE_AFTER, _transport
from app.models import ActionIssue, Device, DeviceEnrollment, Notification, utcnow

CATEGORY = "integration"
ENROLLMENT_PENDING_AFTER = timedelta(minutes=10)

TITLE_NO_CREDENTIAL = "Credenziale agent MikroTik assente"
TITLE_PENDING_ENROLLMENT = "Enrollment MikroTik in attesa"
TITLE_UNKNOWN_TRANSPORT = "Transport agent MikroTik non rilevato"
TITLE_LEGACY_UNREACHABLE = "Agent MikroTik non raggiungibile"


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _open_issue(db, device, title: str, message: str, *, severity: str, details: dict | None = None):
    payload = dict(details or {})
    payload["message"] = message
    existing = db.scalar(
        select(ActionIssue).where(
            ActionIssue.device_id == device.id,
            ActionIssue.category == CATEGORY,
            ActionIssue.title == title,
            ActionIssue.status.in_(["open", "acknowledged"]),
        )
    )
    if existing:
        existing.updated_at = utcnow()
        existing.details = payload
        return False

    db.add(
        ActionIssue(
            category=CATEGORY,
            severity=severity,
            status="open",
            title=title,
            details=payload,
            customer_id=device.customer_id,
            device_id=device.id,
        )
    )
    db.add(
        Notification(
            severity=severity,
            category=CATEGORY,
            title=title,
            message=message,
            customer_id=device.customer_id,
            device_id=device.id,
            source_url=f"/devices/{device.id}/agent",
            is_active=True,
        )
    )
    core.add_event(
        db,
        "AGENT_HEALTH_ISSUE_OPENED",
        customer_id=device.customer_id,
        device_id=device.id,
        details={"title": title, "severity": severity, **payload},
        source="worker",
    )
    return True


def _resolve_issue(db, device, title: str, now):
    issues = list(
        db.scalars(
            select(ActionIssue).where(
                ActionIssue.device_id == device.id,
                ActionIssue.category == CATEGORY,
                ActionIssue.title == title,
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        )
    )
    if not issues:
        return 0

    for issue in issues:
        issue.status = "resolved"
        issue.resolved_at = now
        issue.updated_at = now
        details = dict(issue.details or {})
        details["resolved_by"] = "agent-health-automation"
        issue.details = details

    active_notifications = list(
        db.scalars(
            select(Notification).where(
                Notification.device_id == device.id,
                Notification.category == CATEGORY,
                Notification.title == title,
                Notification.is_active.is_(True),
            )
        )
    )
    for notification in active_notifications:
        notification.is_active = False

    recovery_title = "Recovery agent MikroTik"
    recovery_message = f"Ripristinata la condizione: {title}."
    db.add(
        Notification(
            severity="info",
            category=CATEGORY,
            title=recovery_title,
            message=recovery_message,
            customer_id=device.customer_id,
            device_id=device.id,
            source_url=f"/devices/{device.id}/agent",
            is_active=True,
        )
    )
    core.add_event(
        db,
        "AGENT_HEALTH_RECOVERED",
        customer_id=device.customer_id,
        device_id=device.id,
        details={"resolved_title": title, "resolved_count": len(issues)},
        source="worker",
    )
    return len(issues)


def _emit_missing_recovery_for_legacy_issue(db, now):
    """Add one recovery notification for the historical stale-heartbeat issue.

    Core 0.9 resolves this issue inside backup maintenance. 0.36 keeps that
    proven behavior unchanged and only adds a one-shot recovery notification.
    """
    resolved = list(
        db.scalars(
            select(ActionIssue).where(
                ActionIssue.category == CATEGORY,
                ActionIssue.title == TITLE_LEGACY_UNREACHABLE,
                ActionIssue.status == "resolved",
                ActionIssue.resolved_at.is_not(None),
            )
        )
    )
    created = 0
    for issue in resolved:
        details = dict(issue.details or {})
        if details.get("recovery_notified_at"):
            continue
        device = db.get(Device, issue.device_id) if issue.device_id else None
        if not device:
            continue
        details["recovery_notified_at"] = now.isoformat()
        issue.details = details
        db.add(
            Notification(
                severity="info",
                category=CATEGORY,
                title="Recovery agent MikroTik",
                message="Heartbeat agent nuovamente operativo.",
                customer_id=device.customer_id,
                device_id=device.id,
                source_url=f"/devices/{device.id}/agent",
                is_active=True,
            )
        )
        core.add_event(
            db,
            "AGENT_HEALTH_RECOVERED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={"resolved_title": TITLE_LEGACY_UNREACHABLE},
            source="worker",
        )
        created += 1
    return created


def sync_agent_attention(db, now):
    devices = list(db.scalars(select(Device).where(Device.vendor.ilike("mikrotik"))))
    changed = 0

    for device in devices:
        credential = db.scalar(
            select(DeviceAgentCredential).where(
                DeviceAgentCredential.device_id == device.id,
                DeviceAgentCredential.agent_type == "mikrotik_agent",
                DeviceAgentCredential.is_active.is_(True),
            )
        )
        pending = list(
            db.scalars(
                select(DeviceEnrollment).where(
                    DeviceEnrollment.device_id == device.id,
                    DeviceEnrollment.status == "pending",
                    DeviceEnrollment.expires_at > now,
                )
            )
        )

        if not credential:
            if pending:
                changed += _resolve_issue(db, device, TITLE_NO_CREDENTIAL, now)
                oldest = min(_aware(item.created_at) for item in pending)
                if oldest <= now - ENROLLMENT_PENDING_AFTER:
                    changed += int(
                        _open_issue(
                            db,
                            device,
                            TITLE_PENDING_ENROLLMENT,
                            "Il token di enrollment è ancora pendente oltre la soglia prevista.",
                            severity="warning",
                            details={
                                "pending_count": len(pending),
                                "oldest_pending_at": oldest.isoformat(),
                                "threshold_minutes": int(ENROLLMENT_PENDING_AFTER.total_seconds() / 60),
                            },
                        )
                    )
                else:
                    changed += _resolve_issue(db, device, TITLE_PENDING_ENROLLMENT, now)
            else:
                changed += _resolve_issue(db, device, TITLE_PENDING_ENROLLMENT, now)
                changed += int(
                    _open_issue(
                        db,
                        device,
                        TITLE_NO_CREDENTIAL,
                        "Nessuna credenziale agent attiva e nessun enrollment pendente.",
                        severity="high",
                    )
                )
            changed += _resolve_issue(db, device, TITLE_UNKNOWN_TRANSPORT, now)
            continue

        changed += _resolve_issue(db, device, TITLE_NO_CREDENTIAL, now)
        changed += _resolve_issue(db, device, TITLE_PENDING_ENROLLMENT, now)

        inventory = dict(device.inventory_data or {})
        transport = _transport(inventory)
        heartbeat = _aware(credential.last_used_at or credential.created_at)
        healthy_heartbeat = heartbeat is not None and heartbeat > now - HEARTBEAT_STALE_AFTER
        if transport == "unknown" and healthy_heartbeat:
            changed += int(
                _open_issue(
                    db,
                    device,
                    TITLE_UNKNOWN_TRANSPORT,
                    "L'agent risponde ma il transport effettivo non è ancora identificato.",
                    severity="warning",
                    details={
                        "agent_version": inventory.get("agent_version"),
                        "last_heartbeat": heartbeat.isoformat() if heartbeat else None,
                    },
                )
            )
        else:
            changed += _resolve_issue(db, device, TITLE_UNKNOWN_TRANSPORT, now)

    changed += _emit_missing_recovery_for_legacy_issue(db, now)
    return changed


def agent_health_tick(now=None):
    now = (now or utcnow()).astimezone(timezone.utc)
    with SessionLocal() as db:
        changed = sync_agent_attention(db, now)
        db.commit()
        return {"agent_attention_changes": changed}
