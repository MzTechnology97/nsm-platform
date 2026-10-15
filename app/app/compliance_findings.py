"""Compliance findings handling (COMP-03).

A non-compliance is a ``ComplianceResult`` whose evaluated status is
``fail``. Operators never overwrite that status; they can:

* take it in charge (acknowledge, with a note);
* grant an exception with a justification and an expiry within one year —
  it stays ``fail`` but is *in eccezione* until it expires;
* revoke an exception.

When the evaluation stops failing, handling is cleared automatically (see
``compliance_engine.evaluate_all``). Every action is kept in
``ComplianceResultHistory``. Each Device with failing results that are not
in exception has one Action Center issue, resolved automatically.
"""
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select

from app import main as core
from app.compliance_engine import CONTROLS
from app.compliance_models import RESULT_LABELS, ComplianceResult, ComplianceResultHistory
from app.db import SessionLocal
from app.input_errors import InputError
from app.models import ActionIssue, Customer, Device, User, utcnow
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
ISSUE_TITLE = "Non conformità compliance da gestire"
ISSUE_CATEGORY = "compliance"
MAX_EXCEPTION = timedelta(days=366)
ACTION_LABELS = {
    "evaluated": "Valutazione",
    "acknowledged": "Presa in carico",
    "exception": "Eccezione concessa",
    "exception_revoked": "Eccezione revocata",
    "exception_expired": "Eccezione scaduta",
}


def in_exception(result: ComplianceResult, now: datetime) -> bool:
    return result.status == "fail" and result.exception_until is not None and result.exception_until > now


def unhandled_condition(now: datetime):
    """SQL condition: failing and not covered by an active exception."""
    return (ComplianceResult.status == "fail") & or_(
        ComplianceResult.exception_until.is_(None), ComplianceResult.exception_until <= now
    )


def expire_exceptions(db, now: datetime) -> int:
    expired = list(
        db.scalars(
            select(ComplianceResult).where(
                ComplianceResult.exception_until.is_not(None), ComplianceResult.exception_until <= now
            )
        )
    )
    for result in expired:
        db.add(ComplianceResultHistory(
            result_id=result.id, created_at=now, action="exception_expired", from_status=result.status, to_status=result.status,
            note=f"Eccezione scaduta il {result.exception_until.date().isoformat()}.",
        ))
        result.exception_until = None
        result.exception_reason = None
    return len(expired)


def sync_issues(db, now: datetime) -> dict:
    rows = db.execute(
        select(Device.id, Device.customer_id, func.count(ComplianceResult.id))
        .join(ComplianceResult, ComplianceResult.device_id == Device.id)
        .where(unhandled_condition(now))
        .group_by(Device.id, Device.customer_id)
    ).all()
    wanted = {device_id: (customer_id, count) for device_id, customer_id, count in rows}
    existing = {
        issue.device_id: issue
        for issue in db.scalars(
            select(ActionIssue).where(
                ActionIssue.title == ISSUE_TITLE,
                ActionIssue.category == ISSUE_CATEGORY,
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        )
    }
    stats = {"issues_opened": 0, "issues_resolved": 0}
    for device_id, (customer_id, count) in wanted.items():
        details = {"message": f"{count} controlli di compliance non conformi senza eccezione.", "count": count, "url": f"/compliance?status=fail"}
        issue = existing.get(device_id)
        if issue:
            if (issue.details or {}).get("count") != count:
                issue.details = details
                issue.updated_at = now
            continue
        db.add(ActionIssue(category=ISSUE_CATEGORY, severity="warning", status="open", title=ISSUE_TITLE, details=details, customer_id=customer_id, device_id=device_id))
        stats["issues_opened"] += 1
    for device_id, issue in existing.items():
        if device_id not in wanted:
            issue.status = "resolved"
            issue.resolved_at = now
            issue.updated_at = now
            stats["issues_resolved"] += 1
    return stats


def housekeeping(db, now: datetime) -> dict:
    expired = expire_exceptions(db, now)
    db.flush()
    return {"exceptions_expired": expired, **sync_issues(db, now)}


# -------------------------------------------------------------------- UI --

def _load(db, result_id):
    row = db.execute(
        select(ComplianceResult, Device).join(Device, Device.id == ComplianceResult.device_id).where(ComplianceResult.id == result_id)
    ).first()
    if not row:
        raise HTTPException(404)
    return row


@router.get("/compliance/results/{result_id}", response_class=HTMLResponse, name="compliance_result")
def compliance_result(request: Request, result_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "compliance.read"):
            raise HTTPException(403)
        result, device = _load(db, result_id)
        history = db.execute(
            select(ComplianceResultHistory, User)
            .outerjoin(User, User.id == ComplianceResultHistory.actor_user_id)
            .where(ComplianceResultHistory.result_id == result.id)
            .order_by(ComplianceResultHistory.created_at.desc(), ComplianceResultHistory.id)
        ).all()
        acknowledger = db.get(User, result.acknowledged_by_user_id) if result.acknowledged_by_user_id else None
        now = utcnow()
        return core.render(
            request, db, user, "compliance_result.html",
            result=result, device=device, customer=db.get(Customer, device.customer_id),
            control=CONTROLS.get(result.control_id), history=history, acknowledger=acknowledger,
            result_labels=RESULT_LABELS, action_labels=ACTION_LABELS, in_exception=in_exception(result, now),
            can_manage=core.has_permission(user, "compliance.manage"),
            default_exception_until=(now + timedelta(days=90)).date().isoformat(),
        )


def _acknowledge(db, result, user, now, note: str = "", **_):
    result.acknowledged_by_user_id = user.id
    result.acknowledged_at = now
    db.add(ComplianceResultHistory(result_id=result.id, created_at=now, actor_user_id=user.id, action="acknowledged", from_status="fail", to_status="fail", note=(note or "").strip()[:2000] or None))
    return "acknowledged", "Non conformità presa in carico."


def _exception(db, result, user, now, reason: str = "", until: str = "", **_):
    text = (reason or "").strip()
    if not text:
        raise InputError("reason", "Un'eccezione richiede una motivazione.")
    try:
        day = datetime.fromisoformat(until or "").date()
    except ValueError:
        raise InputError("until", "Indica una data di scadenza valida.")
    expiry = datetime.combine(day, time(23, 59), tzinfo=now.tzinfo)
    if expiry <= now or expiry - now > MAX_EXCEPTION:
        raise InputError("until_range", "La scadenza dell'eccezione deve essere entro un anno da oggi.")
    result.exception_until = expiry
    result.exception_reason = text[:2000]
    db.add(ComplianceResultHistory(result_id=result.id, created_at=now, actor_user_id=user.id, action="exception", from_status="fail", to_status="fail", note=f"{text[:1900]} (fino al {day.isoformat()})"))
    return "exception", f"Eccezione registrata fino al {day.isoformat()}."


def _revoke(db, result, user, now, note: str = "", **_):
    if not result.exception_until:
        raise InputError("no_exception", "Nessuna eccezione attiva.")
    result.exception_until = None
    result.exception_reason = None
    db.add(ComplianceResultHistory(result_id=result.id, created_at=now, actor_user_id=user.id, action="exception_revoked", from_status="fail", to_status="fail", note=(note or "").strip()[:2000] or None))
    return "exception_revoked", "Eccezione revocata: la non conformità torna da gestire."


RESULT_ACTIONS = {"acknowledge": _acknowledge, "exception": _exception, "revoke": _revoke}


def result_action_record(db, user, result_id, action: str, **fields) -> str:
    """Acknowledge, grant or revoke an exception on a failing result; audited, Action Center synced. Shared by both interfaces."""
    handler = RESULT_ACTIONS.get(action)
    if handler is None:
        raise InputError("action", "Azione non valida.")
    result, device = _load(db, result_id)
    now = utcnow()
    if result.status != "fail":
        raise InputError("not_fail", "Il controllo non è in stato non conforme.", 409)
    event, message = handler(db, result, user, now, **fields)
    core.add_event(
        db, f"COMPLIANCE_{event.upper()}", actor=user, customer_id=device.customer_id, device_id=device.id,
        details={"result_id": str(result.id), "control": result.control_id},
    )
    db.flush()
    sync_issues(db, now)
    db.commit()
    return message


def _act(request, result_id, csrf, action, **fields):
    validate_csrf(request, csrf)
    page = f"/compliance/results/{result_id}"
    with SessionLocal() as db:
        user = core.require_permission(request, db, "compliance.manage")
        try:
            message = result_action_record(db, user, result_id, action, **fields)
        except InputError as exc:
            if exc.code == "not_fail":
                return flash_redirect(request, page, "info", str(exc.detail), title="Nessuna modifica")
            return flash_redirect(request, page, "warning", str(exc.detail), title="Dati non validi")
    return flash_redirect(request, page, "success", message, title="Compliance aggiornata")


@router.post("/compliance/results/{result_id}/acknowledge", name="compliance_result_ack")
def compliance_result_ack(request: Request, result_id: uuid.UUID, csrf: str = Form(...), note: str = Form("")):
    return _act(request, result_id, csrf, "acknowledge", note=note)


@router.post("/compliance/results/{result_id}/exception", name="compliance_result_exception")
def compliance_result_exception(request: Request, result_id: uuid.UUID, csrf: str = Form(...), reason: str = Form(""), until: str = Form("")):
    return _act(request, result_id, csrf, "exception", reason=reason, until=until)


@router.post("/compliance/results/{result_id}/exception/revoke", name="compliance_result_exception_revoke")
def compliance_result_exception_revoke(request: Request, result_id: uuid.UUID, csrf: str = Form(...), note: str = Form("")):
    return _act(request, result_id, csrf, "revoke", note=note)


@router.get("/devices/{device_id}/compliance", response_class=HTMLResponse, name="device_compliance")
def device_compliance(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "compliance.read"):
            raise HTTPException(403)
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        now = utcnow()
        order = {"fail": 0, "unknown": 1, "pass": 2, "not_applicable": 3}
        results = sorted(
            db.scalars(select(ComplianceResult).where(ComplianceResult.device_id == device.id)),
            key=lambda r: (order.get(r.status, 9), r.control_id),
        )
        return core.render(
            request, db, user, "device_compliance.html",
            device=device, title=device.display_name or device.device_identity or device.name,
            rows=[(r, CONTROLS.get(r.control_id), in_exception(r, now)) for r in results],
            result_labels=RESULT_LABELS, last_eval=max((r.evaluated_at for r in results), default=None),
        )


def install_compliance_findings(app) -> None:
    app.include_router(router)
