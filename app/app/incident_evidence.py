"""Incident evidence export (INC-04).

Renders one incident (summary, devices, confirmed root cause, hypotheses and
the full timeline) to PDF and stores it in the report archive with its
SHA-256, exactly like the periodic evidence reports, so downloads re-verify
integrity and every export is audited.
"""
from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Form, HTTPException, Request
from sqlalchemy import select

from app import main as core
from app.db import SessionLocal
from app.incident_models import (
    HYPOTHESIS_STATUSES,
    INCIDENT_SEVERITIES,
    INCIDENT_STATUSES,
    ROOT_CAUSE_CATEGORIES,
    Incident,
    IncidentHypothesis,
)
from app.incident_timeline import SOURCE_LABELS, build_timeline
from app.incidents import _incident_devices
from app.models import Customer, Site, utcnow
from app.pdf_writer import PdfDocument
from app.report_builder import DISCLAIMER, _fmt
from app.report_models import GeneratedReport
from app.reports import _platform_name, _slug
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()

REPORT_TYPE = "incident_evidence"
REPORT_TITLE = "Evidenza incidente"
MAX_TIMELINE_ROWS = 1000
CONFIDENCE = {"medium": "media", "low": "bassa"}


def _duration(incident, now) -> str:
    end = incident.resolved_at or now
    minutes = int((end - incident.started_at).total_seconds() // 60)
    if minutes >= 1440:
        text = f"{minutes // 1440} g {(minutes % 1440) // 60} h"
    elif minutes >= 60:
        text = f"{minutes // 60} h {minutes % 60} min"
    else:
        text = f"{minutes} min"
    return text if incident.resolved_at else f"{text} (in corso)"


def render_incident_pdf(db, incident: Incident, *, report_id: str, generated_at, generated_by: str, platform_name: str) -> tuple[bytes, dict]:
    customer = db.get(Customer, incident.customer_id)
    site = db.get(Site, incident.site_id) if incident.site_id else None
    devices = _incident_devices(db, incident.id)
    timeline = build_timeline(db, incident, [device.id for device in devices], generated_at)
    hypotheses = list(
        db.scalars(
            select(IncidentHypothesis)
            .where(IncidentHypothesis.incident_id == incident.id)
            .order_by(IncidentHypothesis.created_at, IncidentHypothesis.id)
        )
    )
    root_cause = next((h for h in hypotheses if h.id == incident.root_cause_hypothesis_id), None)

    doc = PdfDocument(f"{REPORT_TITLE} - {incident.title}")
    doc.heading(REPORT_TITLE, 1)
    doc.key_values(
        [
            ("Piattaforma", platform_name),
            ("Incidente", incident.title),
            ("Cliente", customer.name if customer else "—"),
            ("Sede", site.name if site else "—"),
            ("Gravità", INCIDENT_SEVERITIES.get(incident.severity, incident.severity)),
            ("Stato", INCIDENT_STATUSES.get(incident.status, incident.status)),
            ("Inizio", _fmt(incident.started_at)),
            ("Risoluzione", _fmt(incident.resolved_at) if incident.resolved_at else "non risolto"),
            ("Durata", _duration(incident, generated_at)),
            ("Generato", f"{_fmt(generated_at)} da {generated_by}"),
            ("ID evidenza", report_id),
        ]
    )
    doc.paragraph(DISCLAIMER, size=8.5, gray=0.35)
    if incident.summary:
        doc.heading("Descrizione", 2)
        doc.paragraph(incident.summary)

    doc.heading("1. Apparati coinvolti", 2)
    if devices:
        doc.table(
            ["Apparato", "Vendor", "Modello", "Firmware", "Stato attuale"],
            [[d.display_name or d.device_identity or d.name, d.vendor, d.model or "", d.firmware_version or "", d.status] for d in devices],
            [150, 70, 110, 80, 101],
        )
    else:
        doc.paragraph("Nessun apparato associato: la timeline include solo gli eventi a livello cliente.")

    doc.heading("2. Causa radice", 2)
    if root_cause:
        doc.key_values(
            [
                ("Causa confermata", root_cause.statement),
                ("Categoria", ROOT_CAUSE_CATEGORIES.get(root_cause.category, root_cause.category)),
                ("Confermata il", _fmt(root_cause.decided_at)),
                ("Evidenze indicate", root_cause.decision_note or "—"),
            ]
        )
    else:
        doc.paragraph("Nessuna causa radice confermata da un operatore.", bold=True)
    if hypotheses:
        doc.table(
            ["Ipotesi", "Categoria", "Origine", "Stato", "Decisione"],
            [
                [
                    h.statement,
                    ROOT_CAUSE_CATEGORIES.get(h.category, h.category),
                    f"suggerita ({CONFIDENCE.get(h.confidence, h.confidence)})" if h.origin == "suggested" else "operatore",
                    HYPOTHESIS_STATUSES.get(h.status, h.status),
                    h.decision_note or "",
                ]
                for h in hypotheses
            ],
            [170, 75, 75, 60, 131],
        )
        doc.paragraph(
            "Le ipotesi suggerite derivano da correlazioni temporali automatiche e non costituiscono evidenza di causa.",
            size=8.5,
            gray=0.35,
        )

    doc.heading("3. Timeline", 2)
    doc.paragraph(
        f"Eventi registrati da NSM dal {_fmt(timeline.window_start)} al {_fmt(timeline.window_end)}. "
        "«Fatto» indica un evento registrato dal sistema; «Nota» un'annotazione dell'operatore.",
        size=8.5,
        gray=0.35,
    )
    rows = [
        [
            _fmt(entry.at),
            "Nota" if entry.kind == "operator" else "Fatto",
            SOURCE_LABELS.get(entry.source, entry.source),
            entry.title,
            entry.device_name,
            (entry.detail or "") + (f" ({entry.actor})" if entry.actor else ""),
        ]
        for entry in timeline.entries
    ]
    if rows:
        doc.table(["Quando", "Tipo", "Fonte", "Evento", "Apparato", "Dettaglio"], rows[:MAX_TIMELINE_ROWS], [70, 35, 60, 135, 80, 131])
        if len(rows) > MAX_TIMELINE_ROWS:
            doc.paragraph(f"… altri {len(rows) - MAX_TIMELINE_ROWS} eventi non inclusi.", size=8.5, gray=0.35)
    else:
        doc.paragraph("Nessun evento registrato nella finestra.")
    if timeline.truncated_sources:
        doc.paragraph(f"Fonti con molti eventi, elenco limitato: {', '.join(timeline.truncated_sources)}.", size=8.5, gray=0.35)

    content = doc.render(f"{platform_name} · {REPORT_TITLE} · {report_id}")
    summary = {
        "incident_id": str(incident.id),
        "timeline_entries": len(timeline.entries),
        "operator_notes": sum(1 for entry in timeline.entries if entry.kind == "operator"),
        "devices": len(devices),
        "root_cause_confirmed": root_cause is not None,
        "status": incident.status,
    }
    return content, summary


def archived_exports(db, incident: Incident) -> list[GeneratedReport]:
    reports = db.scalars(
        select(GeneratedReport)
        .where(GeneratedReport.report_type == REPORT_TYPE, GeneratedReport.customer_id == incident.customer_id)
        .order_by(GeneratedReport.generated_at.desc())
    )
    return [report for report in reports if (report.summary or {}).get("incident_id") == str(incident.id)]


@router.post("/incidents/{incident_id}/export", name="incident_export")
def incident_export(request: Request, incident_id: uuid.UUID, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "incidents.read")
        if not core.has_permission(user, "reports.generate"):
            raise HTTPException(403, "Permesso insufficiente.")
        incident = db.get(Incident, incident_id)
        if not incident:
            raise HTTPException(404)
        report_id = uuid.uuid4()
        generated_at = utcnow()
        content, summary = render_incident_pdf(
            db,
            incident,
            report_id=str(report_id),
            generated_at=generated_at,
            generated_by=user.display_name or user.username,
            platform_name=_platform_name(db),
        )
        customer = db.get(Customer, incident.customer_id)
        sha256 = hashlib.sha256(content).hexdigest()
        end = (incident.resolved_at or generated_at).date()
        report = GeneratedReport(
            id=report_id,
            report_type=REPORT_TYPE,
            title=f"{REPORT_TITLE}: {incident.title}"[:200],
            scope_type="customer",
            customer_id=incident.customer_id,
            scope_label=(customer.name if customer else "—")[:200],
            period_start=incident.started_at.date(),
            period_end=max(end, incident.started_at.date()),
            output_format="pdf",
            filename=f"nsm-incidente_{_slug(incident.title)}_{str(report_id)[:8]}.pdf",
            media_type="application/pdf",
            content=content,
            size_bytes=len(content),
            sha256=sha256,
            summary={**summary, "trigger": "manual"},
            generated_at=generated_at,
            generated_by_user_id=user.id,
        )
        db.add(report)
        core.add_event(
            db,
            "INCIDENT_EVIDENCE_EXPORTED",
            actor=user,
            customer_id=incident.customer_id,
            details={"incident_id": str(incident.id), "report_id": str(report_id), "sha256": sha256, "size_bytes": len(content)},
        )
        db.commit()
    return flash_redirect(
        request,
        f"/incidents/{incident_id}#evidence",
        "success",
        f"Evidenza archiviata (SHA-256 {sha256[:12]}…): scaricabile da questa pagina o dall'archivio report.",
        title="Evidenza esportata",
    )


def install_incident_evidence(app) -> None:
    app.include_router(router)
