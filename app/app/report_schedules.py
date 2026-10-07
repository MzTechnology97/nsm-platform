"""REP-03 — scheduled generation of evidence reports for closed periods.

Each schedule produces one report per closed calendar period (previous month,
quarter or year in the application timezone). ``last_period_end`` makes the
generation idempotent across worker ticks; failures back off exponentially,
are audited, and raise one Action Center issue after repeated failures.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app.config import settings
from app.db import SessionLocal
from app.models import ActionIssue, Customer, Notification, utcnow
from app.report_models import FREQUENCY_LABELS, ReportSchedule
from app.reports import FORMATS, REPORTS_PATH, create_report
from app.security import validate_csrf
from app.ui_feedback import exception_message, flash_redirect

log = logging.getLogger("worker")
router = APIRouter()
MAX_BACKOFF = timedelta(hours=6)
BASE_BACKOFF = timedelta(minutes=15)
ISSUE_AFTER_FAILURES = 3
ISSUE_TITLE = "Report pianificato non generato"


def _local_today(now) -> date:
    try:
        return now.astimezone(ZoneInfo(settings.app_timezone)).date()
    except Exception:
        return now.date()


def last_closed_period(frequency: str, today: date) -> tuple[date, date]:
    """Most recent calendar period that ended before ``today``."""
    if frequency == "monthly":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if frequency == "quarterly":
        quarter_start_month = 3 * ((today.month - 1) // 3) + 1
        end = today.replace(month=quarter_start_month, day=1) - timedelta(days=1)
        start_month = 3 * ((end.month - 1) // 3) + 1
        return end.replace(month=start_month, day=1), end
    if frequency == "annual":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    raise ValueError(f"Frequenza non supportata: {frequency}")


def _sync_issue(db, schedule: ReportSchedule, open_issue: bool, message: str = "") -> None:
    issues = [
        issue
        for issue in db.scalars(
            select(ActionIssue).where(
                ActionIssue.category == "system",
                ActionIssue.title == ISSUE_TITLE,
                ActionIssue.status.in_(["open", "acknowledged"]),
            )
        )
        if (issue.details or {}).get("schedule_id") == str(schedule.id)
    ]
    now = utcnow()
    if not open_issue:
        for issue in issues:
            issue.status = "resolved"
            issue.resolved_at = now
            issue.updated_at = now
        return
    details = {"schedule_id": str(schedule.id), "schedule": schedule.name, "message": message}
    if issues:
        issues[0].details = details
        issues[0].updated_at = now
        return
    db.add(
        ActionIssue(
            category="system",
            severity="warning",
            status="open",
            title=ISSUE_TITLE,
            details=details,
            customer_id=schedule.customer_id,
        )
    )
    db.add(
        Notification(
            severity="warning",
            category="system",
            title=ISSUE_TITLE,
            message=f"{schedule.name}: {message}",
            customer_id=schedule.customer_id,
            source_url=REPORTS_PATH,
            is_active=True,
        )
    )


def _record_failure(db, schedule: ReportSchedule, start: date, end: date, error: str, now) -> None:
    failures = schedule.consecutive_failures + 1
    schedule.consecutive_failures = failures
    schedule.last_status = "failed"
    schedule.last_error = error[:500]
    schedule.last_run_at = now
    schedule.next_attempt_at = now + min(BASE_BACKOFF * (2 ** (failures - 1)), MAX_BACKOFF)
    core.add_event(
        db,
        "REPORT_SCHEDULE_FAILED",
        customer_id=schedule.customer_id,
        details={
            "schedule_id": str(schedule.id),
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "error": schedule.last_error,
            "consecutive_failures": failures,
        },
        severity="warning",
        result="failed",
        source="scheduler",
    )
    if failures >= ISSUE_AFTER_FAILURES:
        _sync_issue(db, schedule, True, f"generazione fallita {failures} volte: {schedule.last_error}")


def run_report_schedules(now=None) -> dict[str, int]:
    """Worker entry point: generate the last closed period of every due schedule."""
    now = now or utcnow()
    today = _local_today(now)
    stats = {"generated": 0, "failed": 0}
    with SessionLocal() as db:
        schedule_ids = list(db.scalars(select(ReportSchedule.id).where(ReportSchedule.is_enabled.is_(True))))
    for schedule_id in schedule_ids:
        # One session per schedule: a failing schedule never blocks the others.
        with SessionLocal() as db:
            schedule = db.get(ReportSchedule, schedule_id)
            if not schedule or not schedule.is_enabled:
                continue
            start, end = last_closed_period(schedule.frequency, today)
            if schedule.last_period_end and schedule.last_period_end >= end:
                continue
            if schedule.next_attempt_at and schedule.next_attempt_at > now:
                continue
            customer = db.get(Customer, schedule.customer_id) if schedule.customer_id else None
            try:
                with db.begin_nested():
                    report = create_report(
                        db,
                        customer=customer,
                        period_start=start,
                        period_end=end,
                        output_format=schedule.output_format,
                        schedule_id=schedule.id,
                    )
            except Exception as exc:
                log.exception("Report schedule %s failed", schedule.id)
                _record_failure(db, schedule, start, end, str(exc) or exc.__class__.__name__, now)
                db.commit()
                stats["failed"] += 1
                continue
            schedule.last_period_end = end
            schedule.last_status = "success"
            schedule.last_error = None
            schedule.last_run_at = now
            schedule.last_report_id = report.id
            schedule.consecutive_failures = 0
            schedule.next_attempt_at = None
            _sync_issue(db, schedule, False)
            db.commit()
            stats["generated"] += 1
    return stats


def _feedback(request: Request, exc: HTTPException):
    return flash_redirect(
        request,
        REPORTS_PATH,
        "warning" if exc.status_code == 400 else "error",
        exception_message(exc, "Operazione sulla pianificazione non completata."),
        title="Pianificazione report",
    )


def _schedule(db, schedule_id: uuid.UUID) -> ReportSchedule:
    schedule = db.get(ReportSchedule, schedule_id)
    if not schedule:
        raise HTTPException(400, "Pianificazione non trovata.")
    return schedule


@router.post(f"{REPORTS_PATH}/schedules", name="report_schedule_create")
def schedule_create(
    request: Request,
    csrf: str = Form(...),
    name: str = Form(""),
    customer_id: str = Form(""),
    output_format: str = Form("pdf"),
    frequency: str = Form("monthly"),
):
    try:
        validate_csrf(request, csrf)
        with SessionLocal() as db:
            user = core.require_permission(request, db, "reports.generate")
            label = name.strip()
            if not label:
                raise HTTPException(400, "Indica un nome per la pianificazione.")
            if output_format not in FORMATS:
                raise HTTPException(400, "Formato report non supportato.")
            if frequency not in FREQUENCY_LABELS:
                raise HTTPException(400, "Frequenza non supportata.")
            customer = None
            if customer_id.strip():
                try:
                    customer = db.get(Customer, uuid.UUID(customer_id.strip()))
                except ValueError:
                    customer = None
                if not customer:
                    raise HTTPException(400, "Cliente non valido.")
            schedule = ReportSchedule(
                name=label[:160],
                customer_id=customer.id if customer else None,
                output_format=output_format,
                frequency=frequency,
                created_by_user_id=user.id,
            )
            db.add(schedule)
            db.flush()
            core.add_event(
                db,
                "REPORT_SCHEDULE_CREATED",
                actor=user,
                customer_id=schedule.customer_id,
                details={
                    "schedule_id": str(schedule.id),
                    "name": schedule.name,
                    "frequency": frequency,
                    "format": output_format,
                    "scope": customer.name if customer else "Tutti i clienti",
                },
                source="portal",
            )
            db.commit()
    except HTTPException as exc:
        if exc.status_code in {400, 403}:
            return _feedback(request, exc)
        raise
    return flash_redirect(
        request,
        REPORTS_PATH,
        "success",
        "Pianificazione creata: il primo report copre l'ultimo periodo chiuso e viene generato entro pochi minuti.",
        title="Pianificazione report",
    )


@router.post(f"{REPORTS_PATH}/schedules/{{schedule_id}}/toggle", name="report_schedule_toggle")
def schedule_toggle(request: Request, schedule_id: uuid.UUID, csrf: str = Form(...)):
    try:
        validate_csrf(request, csrf)
        with SessionLocal() as db:
            user = core.require_permission(request, db, "reports.generate")
            schedule = _schedule(db, schedule_id)
            schedule.is_enabled = not schedule.is_enabled
            if schedule.is_enabled:
                schedule.next_attempt_at = None
            enabled = schedule.is_enabled
            core.add_event(
                db,
                "REPORT_SCHEDULE_ENABLED" if enabled else "REPORT_SCHEDULE_DISABLED",
                actor=user,
                customer_id=schedule.customer_id,
                details={"schedule_id": str(schedule.id), "name": schedule.name},
                source="portal",
            )
            db.commit()
    except HTTPException as exc:
        if exc.status_code in {400, 403}:
            return _feedback(request, exc)
        raise
    return flash_redirect(
        request,
        REPORTS_PATH,
        "success",
        "Pianificazione attivata." if enabled else "Pianificazione sospesa.",
        title="Pianificazione report",
    )


@router.post(f"{REPORTS_PATH}/schedules/{{schedule_id}}/delete", name="report_schedule_delete")
def schedule_delete(request: Request, schedule_id: uuid.UUID, csrf: str = Form(...)):
    try:
        validate_csrf(request, csrf)
        with SessionLocal() as db:
            user = core.require_permission(request, db, "reports.generate")
            schedule = _schedule(db, schedule_id)
            core.add_event(
                db,
                "REPORT_SCHEDULE_DELETED",
                actor=user,
                customer_id=schedule.customer_id,
                details={"schedule_id": str(schedule.id), "name": schedule.name},
                source="portal",
            )
            _sync_issue(db, schedule, False)
            db.delete(schedule)
            db.commit()
    except HTTPException as exc:
        if exc.status_code in {400, 403}:
            return _feedback(request, exc)
        raise
    return flash_redirect(
        request,
        REPORTS_PATH,
        "success",
        "Pianificazione eliminata. I report già archiviati restano disponibili.",
        title="Pianificazione report",
    )


def install_report_schedules(app) -> None:
    app.include_router(router)
