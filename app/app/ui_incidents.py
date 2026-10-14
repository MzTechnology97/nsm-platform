"""Action Center and incidents for the new interface (phase 5).

- ``GET  /api/ui/v1/action-center``: open (or chosen) issues, most severe
  first, with category and severity counts, the same filters as the classic
  page (``customer_drilldown.action_center``);
- ``POST /api/ui/v1/action-center/{issue_id}/ack``: take an issue in charge
  (``issues.ack``), audited like the classic button;
- ``GET  /api/ui/v1/incidents``: incidents newest first with status counts and
  the classic filters (``incidents.list_scope``/``status_counts``/``status_filter``);
- ``GET  /api/ui/v1/incidents/{id}``: one incident with its devices, the
  rebuilt timeline, hypotheses and suggested correlations;
- ``POST`` status, notes, devices, hypotheses and hypothesis decisions
  (``incidents.write``), through the same functions as the classic forms.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import case, func, select

from app import incidents as incident_flow
from app import main as core
from app.db import SessionLocal
from app.incident_correlation import suggest
from app.incident_models import Incident, IncidentDevice, IncidentHypothesis
from app.incident_timeline import build_timeline
from app.models import ActionIssue, Customer, Device, Site, User, utcnow
from app.ui_api import brand_icon, require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
ISSUE_PAGE = 100
INCIDENT_PAGE = 50
INCIDENT_DEVICES_SHOWN = 3
OPEN_STATES = ("open", "acknowledged")
SEVERITY_ORDER = case(
    (ActionIssue.severity == "critical", 1),
    (ActionIssue.severity == "high", 2),
    (ActionIssue.severity.in_(["warning", "medium"]), 3),
    (ActionIssue.severity == "low", 4),
    else_=5,
)


def _iso(value):
    return value.isoformat() if value else None


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def device_ref(device) -> dict | None:
    if device is None:
        return None
    return {"id": str(device.id), "name": device.display_name or device.device_identity or device.name, "brand": brand_icon(device),
            "vendor": device.vendor, "status": device.status}


@router.get("/action-center")
def action_center(request: Request, severity: str = "", category: str = "", status: str = "", customer: str = "", offset: int = 0):
    with SessionLocal() as db:
        user = ui_user(request, db, "issues.read")
        customer_id = _uuid(customer)
        base = [ActionIssue.status == status if status else ActionIssue.status.in_(OPEN_STATES)]
        if customer_id:
            base.append(ActionIssue.customer_id == customer_id)
        # Severity chips count within state and customer; category chips also within the chosen severity.
        severities = {name or "info": int(count) for name, count in db.execute(
            select(ActionIssue.severity, func.count(ActionIssue.id)).where(*base).group_by(ActionIssue.severity)).all()}
        filters = [*base, *([ActionIssue.severity == severity] if severity else [])]
        categories = [{"key": name or "system", "count": int(count)} for name, count in db.execute(
            select(ActionIssue.category, func.count(ActionIssue.id)).where(*filters)
            .group_by(ActionIssue.category).order_by(func.count(ActionIssue.id).desc(), ActionIssue.category)).all()]
        if category:
            filters.append(ActionIssue.category == category)
        total = int(db.scalar(select(func.count(ActionIssue.id)).where(*filters)) or 0)
        offset = max(0, int(offset or 0))
        issues = list(db.scalars(select(ActionIssue).where(*filters).order_by(SEVERITY_ORDER, ActionIssue.created_at.desc())
                                 .offset(offset).limit(ISSUE_PAGE)))
        devices = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_({i.device_id for i in issues if i.device_id})))} if issues else {}
        customers = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_({i.customer_id for i in issues if i.customer_id})))} if issues else {}
        return {
            "total": total, "offset": offset, "limit": ISSUE_PAGE, "scope_total": sum(c["count"] for c in categories),
            "categories": categories, "severities": severities,
            "can_ack": core.has_permission(user, "issues.ack"),
            "can_incident": core.has_permission(user, "incidents.write"),
            "items": [{
                "id": str(i.id), "title": i.title, "severity": i.severity, "category": i.category or "system", "status": i.status,
                "created_at": _iso(i.created_at), "updated_at": _iso(i.updated_at),
                "device": device_ref(devices.get(i.device_id)),
                "customer": {"id": str(i.customer_id), "name": customers[i.customer_id].name} if i.customer_id in customers else None,
            } for i in issues],
        }


@router.post("/action-center/{issue_id}/ack", dependencies=[Depends(require_csrf)])
def action_center_ack(request: Request, issue_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "issues.ack")
        issue = db.get(ActionIssue, issue_id)
        if not issue:
            raise HTTPException(404, "Segnalazione non trovata.")
        changed = core.acknowledge_issue_record(db, issue, user)
        db.commit()
        return {"acknowledged": changed, "status": issue.status}


@router.get("/incidents")
def incidents(request: Request, status: str = "active", customer: str = "", q: str = "", offset: int = 0):
    with SessionLocal() as db:
        user = ui_user(request, db, "incidents.read")
        scope = incident_flow.list_scope(_uuid(customer), q.strip()[:200])
        counts = incident_flow.status_counts(db, scope)
        filters = [*scope, *incident_flow.status_filter(status)]
        total = int(db.scalar(select(func.count(Incident.id)).where(*filters)) or 0)
        offset = max(0, int(offset or 0))
        rows = db.execute(
            select(Incident, Customer, IncidentHypothesis)
            .join(Customer, Customer.id == Incident.customer_id)
            .outerjoin(IncidentHypothesis, IncidentHypothesis.id == Incident.root_cause_hypothesis_id)
            .where(*filters).order_by(Incident.started_at.desc(), Incident.id).offset(offset).limit(INCIDENT_PAGE)
        ).all()
        linked: dict = {}
        if rows:
            for incident_id, device in db.execute(
                select(IncidentDevice.incident_id, Device).join(Device, Device.id == IncidentDevice.device_id)
                .where(IncidentDevice.incident_id.in_([incident.id for incident, _, _ in rows]))
                .order_by(Device.display_name.nullslast(), Device.name)
            ).all():
                linked.setdefault(incident_id, []).append(device)
        return {
            "total": total, "offset": offset, "limit": INCIDENT_PAGE,
            "counts": {key: int(counts.get(key, 0)) for key in ("active", *incident_flow.INCIDENT_STATUSES, "all")},
            "can_write": core.has_permission(user, "incidents.write"),
            "items": [{
                "id": str(incident.id), "title": incident.title, "severity": incident.severity, "status": incident.status,
                "started_at": _iso(incident.started_at), "resolved_at": _iso(incident.resolved_at), "updated_at": _iso(incident.updated_at),
                "customer": {"id": str(customer_row.id), "name": customer_row.name},
                "device_count": len(linked.get(incident.id, [])),
                "devices": [device_ref(device) for device in linked.get(incident.id, [])[:INCIDENT_DEVICES_SHOWN]],
                "root_cause": {"statement": hypothesis.statement, "category": hypothesis.category} if hypothesis else None,
            } for incident, customer_row, hypothesis in rows],
        }


def _incident(db, incident_id) -> Incident:
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, {"code": "not_found", "message": "Incidente non trovato."})
    return incident


def _hypothesis_row(hypothesis, author: str, decider: str) -> dict:
    return {
        "id": str(hypothesis.id), "statement": hypothesis.statement, "category": hypothesis.category, "origin": hypothesis.origin,
        "confidence": hypothesis.confidence, "status": hypothesis.status, "evidence": hypothesis.evidence or [],
        "created_at": _iso(hypothesis.created_at), "author": author or None,
        "decided_at": _iso(hypothesis.decided_at), "decider": decider or None, "decision_note": hypothesis.decision_note,
    }


@router.get("/incidents/{incident_id}")
def incident_detail(request: Request, incident_id: uuid.UUID):
    with SessionLocal() as db:
        user = ui_user(request, db, "incidents.read")
        incident = _incident(db, incident_id)
        devices = incident_flow._incident_devices(db, incident.id)
        timeline = build_timeline(db, incident, [device.id for device in devices], utcnow())
        hypotheses = incident_flow._hypotheses(db, incident.id)
        used = {item.get("key") for hypothesis, _, _ in hypotheses for item in (hypothesis.evidence or [])}
        candidates = suggest(incident, timeline, used)
        known = {device.id: device for device in devices}
        missing = {entry.device_id for entry in timeline.entries if entry.device_id and entry.device_id not in known}
        if missing:
            known.update({device.id: device for device in db.scalars(select(Device).where(Device.id.in_(missing)))})
        can_write = core.has_permission(user, "incidents.write")
        customer = db.get(Customer, incident.customer_id)
        site = db.get(Site, incident.site_id) if incident.site_id else None
        creator = db.get(User, incident.created_by_user_id) if incident.created_by_user_id else None

        def entry_device(entry):
            if entry.device_id in known:
                return device_ref(known[entry.device_id])
            return {"id": None, "name": entry.device_name, "brand": None, "vendor": None, "status": None} if entry.device_name else None

        return {
            "incident": {
                "id": str(incident.id), "title": incident.title, "summary": incident.summary, "severity": incident.severity,
                "status": incident.status, "started_at": _iso(incident.started_at), "resolved_at": _iso(incident.resolved_at),
                "created_at": _iso(incident.created_at), "updated_at": _iso(incident.updated_at),
                "created_by": creator.username if creator else None,
                "root_cause_id": str(incident.root_cause_hypothesis_id) if incident.root_cause_hypothesis_id else None,
            },
            "customer": {"id": str(customer.id), "name": customer.name} if customer else None,
            "site": {"id": str(site.id), "name": site.name} if site else None,
            "devices": [device_ref(device) for device in devices],
            "timeline": {
                "window_start": _iso(timeline.window_start), "window_end": _iso(timeline.window_end),
                "truncated_sources": list(timeline.truncated_sources),
                "entries": [{
                    "key": entry.key, "at": _iso(entry.at), "kind": entry.kind, "source": entry.source, "title": entry.title,
                    "detail": entry.detail or None, "severity": entry.severity, "device": entry_device(entry),
                    "url": entry.url, "actor": entry.actor or None,
                } for entry in timeline.entries],
            },
            "hypotheses": [_hypothesis_row(*row) for row in hypotheses],
            "candidates": [{
                "key": candidate.key, "statement": candidate.statement, "category": candidate.category, "confidence": candidate.confidence,
                "reason": candidate.reason, "at": _iso(candidate.entry.at), "source": candidate.entry.source,
            } for candidate in candidates],
            "customer_devices": [device_ref(device) for device in db.scalars(
                select(Device).where(Device.customer_id == incident.customer_id).order_by(Device.display_name.nullslast(), Device.name)
            )] if can_write else [],
            "can_write": can_write,
            "can_export": core.has_permission(user, "reports.generate"),
        }


def _apply(request: Request, incident_id: uuid.UUID, action):
    """Run one incident action as the signed-in writer; input errors become 422 with a code."""
    with SessionLocal() as db:
        user = ui_user(request, db, "incidents.write")
        incident = _incident(db, incident_id)
        try:
            result = action(db, incident, user)
        except incident_flow.IncidentInputError as error:
            raise HTTPException(422, {"code": error.code, "message": error.message}) from None
        db.commit()
        return {**(result or {}), "status": incident.status, "updated_at": _iso(incident.updated_at)}


def _text(body: dict, key: str) -> str:
    value = body.get(key)
    return value if isinstance(value, str) else ""


@router.post("/incidents/{incident_id}/status", dependencies=[Depends(require_csrf)])
def incident_status(request: Request, incident_id: uuid.UUID, body: dict = Body(default={})):
    resolved_at = incident_flow.parse_local(_text(body, "resolved_at"))
    return _apply(request, incident_id, lambda db, incident, user: {
        "changed": incident_flow.change_status(db, incident, user, _text(body, "status"), resolved_at)})


@router.post("/incidents/{incident_id}/notes", dependencies=[Depends(require_csrf)])
def incident_note(request: Request, incident_id: uuid.UUID, body: dict = Body(default={})):
    occurred_at = incident_flow.parse_local(_text(body, "occurred_at"))
    return _apply(request, incident_id, lambda db, incident, user: {
        "note": str(incident_flow.add_note(db, incident, user, _text(body, "body"), _text(body, "kind") or "note", occurred_at).id)})


@router.post("/incidents/{incident_id}/devices", dependencies=[Depends(require_csrf)])
def incident_devices(request: Request, incident_id: uuid.UUID, body: dict = Body(default={})):
    raw = body.get("device_ids")
    wanted = {_uuid(value) for value in raw} if isinstance(raw, list) else set()
    if isinstance(raw, list) and None in wanted:
        raise HTTPException(422, {"code": "foreign_devices", "message": "Apparato non valido."})
    return _apply(request, incident_id, lambda db, incident, user: incident_flow.set_devices(db, incident, user, wanted))


@router.post("/incidents/{incident_id}/hypotheses", dependencies=[Depends(require_csrf)])
def incident_hypothesis_add(request: Request, incident_id: uuid.UUID, body: dict = Body(default={})):
    return _apply(request, incident_id, lambda db, incident, user: {"hypothesis": str(incident_flow.add_hypothesis(
        db, incident, user, _text(body, "statement"), _text(body, "category") or "other", _text(body, "candidate_key")).id)})


@router.post("/incidents/{incident_id}/hypotheses/{hypothesis_id}/decision", dependencies=[Depends(require_csrf)])
def incident_hypothesis_decision(request: Request, incident_id: uuid.UUID, hypothesis_id: uuid.UUID, body: dict = Body(default={})):
    def decide(db, incident, user):
        hypothesis = db.get(IncidentHypothesis, hypothesis_id)
        if not hypothesis or hypothesis.incident_id != incident.id:
            raise HTTPException(404, {"code": "not_found", "message": "Ipotesi non trovata."})
        incident_flow.decide_hypothesis(db, incident, hypothesis, user, _text(body, "decision"), _text(body, "note"))
        return {"hypothesis_status": hypothesis.status}

    return _apply(request, incident_id, decide)


def install_ui_incidents(app) -> None:
    app.include_router(router)
