"""REP-01/REP-02 — manual operational evidence reports with hashed archive.

Operators generate a PDF or CSV report for all Customers or one Customer over a
date range.  The rendered output is archived immutably with its SHA-256 and a
generation audit event; downloads re-verify the hash before serving.
"""
from __future__ import annotations

import hashlib
import math
import re
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select

from app import main as core
from app.config import settings
from app.db import SessionLocal
from app.models import Customer, utcnow
from app.preferences import PlatformBranding
from app.report_builder import REPORT_TITLE, REPORT_TYPE, collect_report_data, render_csv, render_pdf, summary
from app.report_models import FREQUENCY_LABELS, GeneratedReport, ReportSchedule
from app.security import validate_csrf
from app.ui_feedback import exception_message, flash_redirect

router = APIRouter()
FORMATS = {"pdf": "application/pdf", "csv": "text/csv; charset=utf-8"}
MAX_PERIOD_DAYS = 366
DEFAULT_PERIOD_DAYS = 30
PAGE_SIZE = 25
REPORTS_PATH = "/audit/reports"


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        raise HTTPException(400, f"Data {label} non valida.")


def _platform_name(db) -> str:
    branding = db.get(PlatformBranding, 1)
    return (branding.portal_name if branding and branding.portal_name else None) or settings.app_name


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")[:50] or "report"


def create_report(
    db,
    *,
    customer: Customer | None,
    period_start: date,
    period_end: date,
    output_format: str,
    actor=None,
    schedule_id: uuid.UUID | None = None,
) -> GeneratedReport:
    """Render, archive and audit one report. The caller commits."""
    report_id = uuid.uuid4()
    generated_at = utcnow()
    trigger = "schedule" if schedule_id else "manual"
    data = collect_report_data(db, customer=customer, period_start=period_start, period_end=period_end)
    if output_format == "pdf":
        content = render_pdf(
            data,
            report_id=str(report_id),
            generated_at=generated_at,
            generated_by=(actor.display_name or actor.username) if actor else "pianificazione NSM",
            platform_name=_platform_name(db),
        )
    else:
        content = render_csv(data)
    scope_label = customer.name if customer else "Tutti i clienti"
    filename = (
        f"nsm-report_{_slug(scope_label)}_{period_start.isoformat()}_{period_end.isoformat()}"
        f"_{str(report_id)[:8]}.{output_format}"
    )
    sha256 = hashlib.sha256(content).hexdigest()
    report = GeneratedReport(
        id=report_id,
        report_type=REPORT_TYPE,
        title=REPORT_TITLE,
        scope_type="customer" if customer else "all",
        customer_id=customer.id if customer else None,
        scope_label=scope_label[:200],
        period_start=period_start,
        period_end=period_end,
        output_format=output_format,
        filename=filename,
        media_type=FORMATS[output_format],
        content=content,
        size_bytes=len(content),
        sha256=sha256,
        summary={**summary(data), "trigger": trigger},
        generated_at=generated_at,
        generated_by_user_id=actor.id if actor else None,
        schedule_id=schedule_id,
    )
    db.add(report)
    core.add_event(
        db,
        "REPORT_GENERATED",
        actor=actor,
        customer_id=customer.id if customer else None,
        details={
            "report_id": str(report_id),
            "report_type": REPORT_TYPE,
            "format": output_format,
            "scope": scope_label,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "sha256": sha256,
            "size_bytes": len(content),
            "trigger": trigger,
            "schedule_id": str(schedule_id) if schedule_id else None,
        },
        source="scheduler" if schedule_id else "portal",
    )
    return report


@router.get(REPORTS_PATH, response_class=HTMLResponse, name="reports_archive")
def reports_archive(request: Request, page: int = 1):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "reports.read"):
            raise HTTPException(403)
        total = db.scalar(select(func.count(GeneratedReport.id))) or 0
        pages = max(1, math.ceil(total / PAGE_SIZE))
        page = min(max(1, page), pages)
        reports = list(
            db.scalars(
                select(GeneratedReport)
                .order_by(GeneratedReport.generated_at.desc())
                .offset((page - 1) * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
        )
        today = utcnow().date()
        return core.render(
            request,
            db,
            user,
            "reports_archive.html",
            reports=reports,
            customers=list(db.scalars(select(Customer).where(Customer.is_active.is_(True)).order_by(Customer.name))),
            default_start=(today - timedelta(days=DEFAULT_PERIOD_DAYS - 1)).isoformat(),
            default_end=today.isoformat(),
            can_generate=core.has_permission(user, "reports.generate"),
            schedules=list(db.scalars(select(ReportSchedule).order_by(ReportSchedule.created_at))),
            schedule_customers={customer.id: customer.name for customer in db.scalars(select(Customer))},
            frequencies=FREQUENCY_LABELS,
            page=page,
            pages=pages,
            total=total,
        )


@router.post(f"{REPORTS_PATH}/generate", name="reports_generate")
def reports_generate(
    request: Request,
    csrf: str = Form(...),
    customer_id: str = Form(""),
    period_start: str = Form(""),
    period_end: str = Form(""),
    output_format: str = Form("pdf"),
):
    try:
        validate_csrf(request, csrf)
        with SessionLocal() as db:
            user = core.require_permission(request, db, "reports.generate")
            if output_format not in FORMATS:
                raise HTTPException(400, "Formato report non supportato.")
            start = _parse_date(period_start, "di inizio")
            end = _parse_date(period_end, "di fine")
            if end < start:
                raise HTTPException(400, "La data di fine precede la data di inizio.")
            if (end - start).days + 1 > MAX_PERIOD_DAYS:
                raise HTTPException(400, f"Il periodo massimo è di {MAX_PERIOD_DAYS} giorni.")
            if end > utcnow().date():
                raise HTTPException(400, "Il periodo non può terminare nel futuro.")
            customer = None
            if customer_id.strip():
                try:
                    customer = db.get(Customer, uuid.UUID(customer_id.strip()))
                except ValueError:
                    customer = None
                if not customer:
                    raise HTTPException(400, "Cliente non valido.")

            report = create_report(
                db,
                customer=customer,
                period_start=start,
                period_end=end,
                output_format=output_format,
                actor=user,
            )
            sha256 = report.sha256
            db.commit()
    except HTTPException as exc:
        if exc.status_code in {400, 403}:
            return flash_redirect(
                request,
                REPORTS_PATH,
                "warning" if exc.status_code == 400 else "error",
                exception_message(exc, "Report non generato."),
                title="Report non generato",
            )
        raise
    return flash_redirect(
        request,
        REPORTS_PATH,
        "success",
        f"Report {output_format.upper()} generato e archiviato (SHA-256 {sha256[:16]}…).",
        title="Report generato",
    )


@router.get(f"{REPORTS_PATH}/{{report_id}}/download", name="reports_download")
def reports_download(request: Request, report_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "reports.read"):
            raise HTTPException(403)
        report = db.get(GeneratedReport, report_id)
        if not report:
            return flash_redirect(request, REPORTS_PATH, "warning", "Report non trovato.", title="Report non disponibile")
        if hashlib.sha256(report.content).hexdigest() != report.sha256:
            core.add_event(
                db,
                "REPORT_INTEGRITY_FAILED",
                actor=user,
                customer_id=report.customer_id,
                details={"report_id": str(report.id), "expected_sha256": report.sha256},
                severity="high",
                result="failed",
                source="portal",
            )
            db.commit()
            return flash_redirect(
                request,
                REPORTS_PATH,
                "error",
                "Il contenuto archiviato non corrisponde allo SHA-256 registrato: download bloccato.",
                title="Integrità report non verificata",
            )
        core.add_event(
            db,
            "REPORT_DOWNLOADED",
            actor=user,
            customer_id=report.customer_id,
            details={"report_id": str(report.id), "sha256": report.sha256},
            source="portal",
        )
        db.commit()
        return Response(
            content=report.content,
            media_type=report.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{report.filename}"',
                "X-Content-SHA256": report.sha256,
                "Cache-Control": "no-store",
            },
        )


def install_reports(app) -> None:
    app.include_router(router)
