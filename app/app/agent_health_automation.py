"""Automated Action Center and recovery notifications for MikroTik agent health."""
from __future__ import annotations

from datetime import timedelta, timezone

from sqlalchemy import select

from app import main as core
from app.agent_models import DeviceAgentCredential
from app.db import SessionLocal
from app.mikrotik_agent_status import HEARTBEAT_STALE_AFTER, _installation_state, _transport
from app.models import ActionIssue, Device, DeviceEnrollment, Notification, utcnow

CATEGORY = "integration"
ENROLLMENT_PENDING_AFTER = timedelta(minutes=10)
ENROLLMENT_HISTORY_WINDOW = timedelta(days=30)

TITLE_NO_CREDENTIAL = "Credenziale agent MikroTik assente"
TITLE_PENDING_ENROLLMENT = "Enrollment MikroTik in attesa"
TITLE_TOKEN_EXPIRED = "Token enrollment MikroTik scaduto"
TITLE_INSTALL_SUSPECT = "Installazione agent MikroTik incompleta"
TITLE_UNKNOWN_TRANSPORT = "Transport agent MikroTik non rilevato"
TITLE_LEGACY_UNREACHABLE = "Agent MikroTik non raggiungibile"


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _historical_credential_activity(credential, history, now):
    """Return recent heartbeat evidence for pre-install-verification agents.

    Before Core 0.41 some agents updated ``credential.last_used_at`` without
    persisting ``device.last_seen`` or enrollment history. That signal remains
    valid only when no retained enrollment history exists. New pairings must
    still prove a real post-pairing heartbeat through ``_installation_state``.
    """
    if not credential or history:
        return False
    created_at = _aware(credential.created_at)
    last_used_at = _aware(credential.last_used_at)
    if not created_at or not last_used_at:
        return False
    return bool(
        last_used_at > created_at + timedelta(seconds=1)
        and last_used_at > now - HEARTBEAT_STALE_AFTER
    )


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
    """Add one recovery notification for the historical stale-heartbeat issue."""
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
    if not devices:
        return _emit_missing_recovery_for_legacy_issue(db, now)

    device_ids = [device.id for device in devices]
    credentials = {
        credential.device_id: credential
        for credential in db.scalars(
            select(DeviceAgentCredential).where(
                DeviceAgentCredential.device_id.in_(device_ids),
                DeviceAgentCredential.agent_type == "mikrotik_agent",
                DeviceAgentCredential.is_active.is_(True),
            )
        )
    }
    enrollment_history = {}
    enrollments = list(
        db.scalars(
            select(DeviceEnrollment)
            .where(
                DeviceEnrollment.device_id.in_(device_ids),
                DeviceEnrollment.created_at >= now - ENROLLMENT_HISTORY_WINDOW,
            )
            .order_by(DeviceEnrollment.device_id, DeviceEnrollment.created_at.desc())
        )
    )
    for enrollment in enrollments:
        history = enrollment_history.setdefault(enrollment.device_id, [])
        if len(history) < 10:
            history.append(enrollment)

    for device in devices:
        credential = credentials.get(device.id)
        history = enrollment_history.get(device.id, [])
        installation = _installation_state(device, credential, history)
        historical_activity = _historical_credential_activity(credential, history, now)
        pending = [
            item
            for item in history
            if item.status == "pending"
            and _aware(item.expires_at)
            and _aware(item.expires_at) > now
        ]

        if not credential:
            changed += _resolve_issue(db, device, TITLE_INSTALL_SUSPECT, now)
            changed += _resolve_issue(db, device, TITLE_UNKNOWN_TRANSPORT, now)

            if installation["key"] == "token_expired":
                changed += _resolve_issue(db, device, TITLE_PENDING_ENROLLMENT, now)
                changed += _resolve_issue(db, device, TITLE_NO_CREDENTIAL, now)
                changed += int(
                    _open_issue(
                        db,
                        device,
                        TITLE_TOKEN_EXPIRED,
                        "Il token one-shot di enrollment è scaduto senza completare il pairing.",
                        severity="warning",
                        details={"install_state": installation["key"]},
                    )
                )
                continue

            changed += _resolve_issue(db, device, TITLE_TOKEN_EXPIRED, now)
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
            continue

        changed += _resolve_issue(db, device, TITLE_NO_CREDENTIAL, now)
        changed += _resolve_issue(db, device, TITLE_PENDING_ENROLLMENT, now)
        changed += _resolve_issue(db, device, TITLE_TOKEN_EXPIRED, now)

        if installation["key"] in {"install_suspect", "credential_no_heartbeat"} and not historical_activity:
            changed += int(
                _open_issue(
                    db,
                    device,
                    TITLE_INSTALL_SUSPECT,
                    "Il pairing è completato ma l'agent non ha prodotto un heartbeat post-installazione entro la soglia prevista.",
                    severity="critical",
                    details={
                        "install_state": installation["key"],
                        "paired_at": installation.get("paired_at").isoformat() if installation.get("paired_at") else None,
                        "grace_minutes": installation.get("grace_minutes"),
                    },
                )
            )
        else:
            changed += _resolve_issue(db, device, TITLE_INSTALL_SUSPECT, now)

        inventory = dict(device.inventory_data or {})
        transport = _transport(inventory)
        last_seen = _aware(device.last_seen)
        post_pairing_heartbeat = bool(
            installation.get("heartbeat_after_pairing")
            and last_seen
            and last_seen > now - HEARTBEAT_STALE_AFTER
        )
        healthy_heartbeat = post_pairing_heartbeat or historical_activity
        observed_heartbeat = last_seen
        if historical_activity and not observed_heartbeat:
            observed_heartbeat = _aware(credential.last_used_at)

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
                        "last_heartbeat": observed_heartbeat.isoformat() if observed_heartbeat else None,
                        "heartbeat_evidence": "credential_last_used" if historical_activity and not post_pairing_heartbeat else "device_last_seen",
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
