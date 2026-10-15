"""REP-01/REP-02 — manual operational evidence reports with hashed archive.

Operators generate a PDF or CSV report for all Customers or one Customer over a
date range.  The rendered output is archived immutably with its SHA-256 and a
generation audit event; downloads re-verify the hash before serving.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app import main as core
from app.config import settings
from app.db import SessionLocal
from app.models import Customer, utcnow
from app.preferences import PlatformBranding
from app.report_builder import REPORT_TITLE, REPORT_TYPE, collect_report_data, render_csv, render_pdf, summary
from app.report_models import GeneratedReport

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


def generate_checked(db, user, customer_id: str, period_start: str, period_end: str, output_format: str) -> GeneratedReport:
    """Validate the request like the classic form (HTTPException 400 with the reason) and create the report. Caller commits."""
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
    return create_report(db, customer=customer, period_start=start, period_end=end, output_format=output_format, actor=user)


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
            return RedirectResponse("/app/reports?error=not_found", status_code=303)
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
            # The archived content no longer matches its SHA-256: the reports page explains the block.
            return RedirectResponse("/app/reports?error=integrity", status_code=303)
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
