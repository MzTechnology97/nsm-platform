"""Remediation of EOL/EOS Devices (LIFE-03).

Every Device whose lifecycle state is EOL or EOS gets a remediation record:
*da gestire* until an operator plans the replacement (with a target date),
grants a justified exception with an expiry, records the replacement Device or
decommissions it.  Each decision is kept in a history with author and note and
audited.  An Action Center issue stays open while the Device is still to be
handled, its planned replacement is overdue or its exception has expired.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app.db import SessionLocal
from app.models import ActionIssue, Device, LifecycleRemediation, LifecycleRemediationHistory, utcnow
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
ATTENTION_STATES = ("eol", "eos")
STATUS_LABELS = {
    "open": "Da gestire",
    "planned": "Sostituzione pianificata",
    "exception": "In eccezione",
    "replaced": "Sostituito",
    "decommissioned": "Dismesso",
}
CLOSED = ("replaced", "decommissioned")
ISSUE_TITLE = "Apparato fuori supporto vendor da gestire"
ISSUE_CATEGORY = "lifecycle"
MAX_EXCEPTION_DAYS = 365


def _history(db, rem, action, from_status, to_status, note=None, actor=None):
    db.add(LifecycleRemediationHistory(
        remediation_id=rem.id, actor_user_id=actor.id if actor else None, action=action,
        from_status=from_status, to_status=to_status, note=(note or None) and note[:2000],
    ))


def attention_reason(rem: LifecycleRemediation, device: Device, today: date) -> str | None:
    """Why this Device still needs an operator decision, or None."""
    if str(device.lifecycle_status or "").lower() not in ATTENTION_STATES:
        return None
    if rem.status == "open":
        return "Nessuna decisione registrata (piano di sostituzione, eccezione o dismissione)."
    if rem.status == "planned" and rem.target_date and rem.target_date < today:
        return f"Sostituzione pianificata entro il {rem.target_date.isoformat()} non ancora registrata."
    return None


def housekeeping(db, now: datetime) -> dict:
    """Create missing records, expire exceptions and keep Action Center issues in sync."""
    today = now.date()
    stats = {"created": 0, "expired": 0, "issues_opened": 0, "issues_resolved": 0}
    devices = {d.id: d for d in db.scalars(select(Device).where(Device.lifecycle_status.in_(ATTENTION_STATES)))}
    records = {r.device_id: r for r in db.scalars(select(LifecycleRemediation))}
    for device_id, device in devices.items():
        if device_id not in records:
            rem = LifecycleRemediation(id=uuid.uuid4(), device_id=device_id, status="open", created_at=now, updated_at=now)
            db.add(rem)
            db.flush()
            _history(db, rem, "created", None, "open", f"Stato lifecycle {device.lifecycle_status.upper()}.")
            records[device_id] = rem
            stats["created"] += 1
    for rem in records.values():
        if rem.status == "exception" and rem.exception_until and rem.exception_until < today:
            _history(db, rem, "exception_expired", "exception", "open", f"Eccezione scaduta il {rem.exception_until.isoformat()}.")
            rem.status = "open"
            rem.updated_at = now
            stats["expired"] += 1
    wanted = {}
    for device_id, rem in records.items():
        device = devices.get(device_id)
        reason = attention_reason(rem, device, today) if device else None
        if reason:
            wanted[device_id] = (device, reason)
    existing = {
        issue.device_id: issue
        for issue in db.scalars(select(ActionIssue).where(
            ActionIssue.title == ISSUE_TITLE, ActionIssue.category == ISSUE_CATEGORY,
            ActionIssue.status.in_(["open", "acknowledged"]),
        ))
    }
    for device_id, (device, reason) in wanted.items():
        severity = "critical" if device.lifecycle_status == "eos" else "warning"
        details = {"message": reason, "lifecycle_status": device.lifecycle_status, "url": f"/devices/{device_id}/lifecycle"}
        issue = existing.get(device_id)
        if issue:
            if issue.details != details or issue.severity != severity:
                issue.details, issue.severity, issue.updated_at = details, severity, now
            continue
        db.add(ActionIssue(category=ISSUE_CATEGORY, severity=severity, status="open", title=ISSUE_TITLE, details=details,
                           customer_id=device.customer_id, device_id=device_id))
        stats["issues_opened"] += 1
    for device_id, issue in existing.items():
        if device_id not in wanted:
            issue.status, issue.resolved_at, issue.updated_at = "resolved", now, now
            stats["issues_resolved"] += 1
    return stats


def summary(db, device_ids=None, today: date | None = None) -> dict:
    """Remediation figures for reports and worklists."""
    today = today or utcnow().date()
    query = select(LifecycleRemediation, Device).join(Device, Device.id == LifecycleRemediation.device_id)
    if device_ids is not None:
        query = query.where(LifecycleRemediation.device_id.in_(list(device_ids) or [None]))
    counts = {key: 0 for key in STATUS_LABELS}
    to_handle, exceptions, overdue = [], [], 0
    for rem, device in db.execute(query):
        if str(device.lifecycle_status or "").lower() not in ATTENTION_STATES and rem.status not in CLOSED:
            continue
        counts[rem.status] = counts.get(rem.status, 0) + 1
        reason = attention_reason(rem, device, today)
        if reason:
            to_handle.append((device, rem, reason))
            if rem.status == "planned":
                overdue += 1
        if rem.status == "exception":
            exceptions.append((device, rem))
    return {"counts": counts, "to_handle": to_handle, "exceptions": exceptions, "overdue": overdue}


def _parse_date(value, label):
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Indica {label}.")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{label.capitalize()} non valida.") from None


@router.post("/devices/{device_id}/lifecycle/remediation", name="lifecycle_remediation_update")
async def remediation_update(request: Request, device_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    back = f"/devices/{device_id}/lifecycle"
    with SessionLocal() as db:
        user = core.require_permission(request, db, "lifecycle.manage")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        now = utcnow()
        today = now.date()
        housekeeping(db, now)
        rem = db.scalar(select(LifecycleRemediation).where(LifecycleRemediation.device_id == device.id))
        if rem is None:
            return flash_redirect(request, back, "warning", "L'apparato non è EOL/EOS: non c'è nulla da gestire.", title="Nessuna azione")
        action = str(form.get("action") or "")
        note = str(form.get("note") or "").strip()
        previous = rem.status
        try:
            if action == "plan":
                target = _parse_date(form.get("target_date"), "la data prevista di sostituzione")
                if target < today:
                    raise ValueError("La data prevista non può essere nel passato.")
                if len(note) < 5:
                    raise ValueError("Descrivi il piano di sostituzione (almeno 5 caratteri).")
                rem.status, rem.target_date, rem.plan_note = "planned", target, note
                rem.exception_until = rem.exception_reason = None
                event, message = "LIFECYCLE_REPLACEMENT_PLANNED", f"Sostituzione pianificata entro il {target.isoformat()}."
            elif action == "exception":
                until = _parse_date(form.get("exception_until"), "la scadenza dell'eccezione")
                if until <= today or until > today + timedelta(days=MAX_EXCEPTION_DAYS):
                    raise ValueError("La scadenza dell'eccezione deve essere tra domani e un anno.")
                if len(note) < 10:
                    raise ValueError("Motiva l'eccezione (almeno 10 caratteri).")
                rem.status, rem.exception_until, rem.exception_reason = "exception", until, note
                event, message = "LIFECYCLE_EXCEPTION_GRANTED", f"Eccezione valida fino al {until.isoformat()}."
            elif action == "replaced":
                try:
                    replacement = db.get(Device, uuid.UUID(str(form.get("replacement_device_id") or "")))
                except ValueError:
                    replacement = None
                if not replacement or replacement.id == device.id or replacement.customer_id != device.customer_id:
                    raise ValueError("Seleziona l'apparato che ha sostituito questo (stesso cliente).")
                rem.status, rem.replacement_device_id, rem.plan_note = "replaced", replacement.id, note or rem.plan_note
                event = "LIFECYCLE_DEVICE_REPLACED"
                message = f"Sostituito da {replacement.display_name or replacement.device_identity or replacement.name}."
            elif action == "decommissioned":
                if len(note) < 5:
                    raise ValueError("Indica come è stato dismesso l'apparato (almeno 5 caratteri).")
                rem.status, rem.plan_note = "decommissioned", note
                event, message = "LIFECYCLE_DEVICE_DECOMMISSIONED", "Apparato registrato come dismesso."
            elif action == "reopen":
                rem.status = "open"
                rem.exception_until = rem.exception_reason = None
                event, message = "LIFECYCLE_REMEDIATION_REOPENED", "Gestione riaperta."
            else:
                raise ValueError("Azione non valida.")
        except ValueError as exc:
            db.rollback()
            return flash_redirect(request, back, "warning", str(exc), title="Gestione non salvata")
        rem.decided_by_user_id = user.id
        rem.updated_at = now
        _history(db, rem, action, previous, rem.status, note or message, user)
        core.add_event(db, event, actor=user, customer_id=device.customer_id, device_id=device.id,
                       details={"from": previous, "to": rem.status, "note": note[:500] or None,
                                "target_date": rem.target_date and rem.target_date.isoformat(),
                                "exception_until": rem.exception_until and rem.exception_until.isoformat(),
                                "replacement_device_id": str(rem.replacement_device_id) if rem.replacement_device_id else None})
        db.flush()
        housekeeping(db, now)
        final_status = rem.status
        db.commit()
    return flash_redirect(request, back, "success", message, title=STATUS_LABELS[final_status])


def page_context(db, device) -> dict:
    """Remediation panel context for the Device lifecycle page."""
    rem = db.scalar(select(LifecycleRemediation).where(LifecycleRemediation.device_id == device.id))
    history = []
    replacement = None
    if rem:
        history = list(db.scalars(select(LifecycleRemediationHistory).where(LifecycleRemediationHistory.remediation_id == rem.id)
                                  .order_by(LifecycleRemediationHistory.created_at.desc()).limit(20)))
        replacement = db.get(Device, rem.replacement_device_id) if rem.replacement_device_id else None
    candidates = list(db.scalars(select(Device).where(Device.customer_id == device.customer_id, Device.id != device.id)
                                 .order_by(Device.display_name.nullslast(), Device.name)))
    today = utcnow().date()
    return {
        "remediation": rem,
        "remediation_history": history,
        "remediation_labels": STATUS_LABELS,
        "remediation_reason": attention_reason(rem, device, today) if rem else None,
        "replacement": replacement,
        "replacement_candidates": candidates,
        "max_exception": (today + timedelta(days=MAX_EXCEPTION_DAYS)).isoformat(),
    }


def install_lifecycle_remediation(app) -> None:
    app.include_router(router)
