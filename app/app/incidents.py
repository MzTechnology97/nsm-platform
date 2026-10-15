"""Incident workflow (INC-01/INC-02): list, create, timeline, notes, status."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select

from app import main as core
from app.config import settings
from app.input_errors import InputError
from app.incident_correlation import suggest
from app.incident_models import (
    INCIDENT_SEVERITIES,
    INCIDENT_STATUSES,
    NOTE_KINDS,
    ROOT_CAUSE_CATEGORIES,
    Incident,
    IncidentDevice,
    IncidentHypothesis,
    IncidentNote,
)
from app.incident_timeline import build_timeline
from app.models import Customer, Device, Site, User, utcnow


ACTIVE_STATUSES = ("open", "investigating", "monitoring")
PER_PAGE = 50
FUTURE_TOLERANCE = timedelta(minutes=5)


def _tz():
    try:
        return ZoneInfo(settings.app_timezone)
    except Exception:
        return ZoneInfo("UTC")


def parse_local(value: str) -> datetime | None:
    """``datetime-local`` input (platform timezone) → aware datetime."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_tz())


def _uuid(value: str):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _incident_devices(db, incident_id) -> list[Device]:
    return list(
        db.scalars(
            select(Device)
            .join(IncidentDevice, IncidentDevice.device_id == Device.id)
            .where(IncidentDevice.incident_id == incident_id)
            .order_by(Device.display_name.nullslast(), Device.name)
        )
    )


def list_scope(customer_id, term: str) -> list:
    """Customer and text filters shared by the classic list and the new interface."""
    scope = []
    if customer_id:
        scope.append(Incident.customer_id == customer_id)
    if term:
        scope.append(or_(Incident.title.ilike(f"%{term}%"), Incident.summary.ilike(f"%{term}%")))
    return scope


def status_counts(db, scope: list) -> dict:
    counts = dict(db.execute(select(Incident.status, func.count(Incident.id)).where(*scope).group_by(Incident.status)).all())
    counts["active"] = sum(counts.get(key, 0) for key in ACTIVE_STATUSES)
    counts["all"] = sum(value for key, value in counts.items() if key in INCIDENT_STATUSES)
    return counts


def status_filter(status: str) -> list:
    if status == "active":
        return [Incident.status.in_(ACTIVE_STATUSES)]
    if status in INCIDENT_STATUSES:
        return [Incident.status == status]
    return []


# ------------------------------------------------------------------ list --

def create_incident_record(db, user, *, customer_id, title, summary, severity, started_at, site_id, device_ids) -> uuid.UUID:
    """Open an incident for one customer with its devices; audited, commits. Shared by both interfaces.

    Raises ``InputError`` with codes ``customer``, ``title``, ``started``, ``devices``.
    """
    customer = db.get(Customer, _uuid(customer_id)) if _uuid(customer_id) else None
    if not customer:
        raise InputError("customer", "Seleziona il cliente.")
    title = str(title or "").strip()
    if not title:
        raise InputError("title", "Il titolo è obbligatorio.")
    started = parse_local(str(started_at or ""))
    now = utcnow()
    if started is None or started > now + FUTURE_TOLERANCE:
        raise InputError("started", "Indica un inizio valido, non nel futuro.")
    severity = str(severity or "medium")
    if severity not in INCIDENT_SEVERITIES:
        severity = "medium"
    wanted = {_uuid(value) for value in (device_ids or [])} - {None}
    devices = list(db.scalars(select(Device).where(Device.id.in_(wanted), Device.customer_id == customer.id))) if wanted else []
    if len(devices) != len(wanted):
        raise InputError("devices", "Gli apparati devono appartenere al cliente selezionato.")
    site = _uuid(site_id)
    if site and not db.scalar(select(Site.id).where(Site.id == site, Site.customer_id == customer.id)):
        site = None
    incident = Incident(
        id=uuid.uuid4(),
        customer_id=customer.id,
        site_id=site,
        title=title[:200],
        summary=str(summary or "").strip() or None,
        severity=severity,
        status="open",
        started_at=started,
        created_by_user_id=user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(incident)
    db.flush()
    for device in devices:
        db.add(IncidentDevice(incident_id=incident.id, device_id=device.id, added_at=now))
    core.add_event(
        db,
        "INCIDENT_CREATED",
        actor=user,
        customer_id=customer.id,
        details={"incident_id": str(incident.id), "title": incident.title, "severity": severity, "devices": len(devices)},
    )
    db.commit()
    return incident.id


class IncidentInputError(ValueError):
    """Rejected operator input. ``message`` is the text the classic page flashes; ``code`` is for the new interface."""

    def __init__(self, code: str, message: str, title: str = "Dati non validi"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.title = title


def change_status(db, incident, user, status: str, resolved_at: datetime | None) -> bool:
    """Move the incident to ``status`` (audited). False when it already was there. Caller commits."""
    if status not in INCIDENT_STATUSES:
        raise IncidentInputError("invalid_status", "Stato non valido.")
    now = utcnow()
    previous = incident.status
    if status == "resolved":
        when = resolved_at or now
        if when < incident.started_at or when > now + FUTURE_TOLERANCE:
            raise IncidentInputError("invalid_resolved_at", "La risoluzione deve essere successiva all'inizio e non nel futuro.")
        incident.resolved_at = when
    else:
        incident.resolved_at = None
    if status == previous and status != "resolved":
        return False
    incident.status = status
    incident.updated_at = now
    core.add_event(
        db,
        "INCIDENT_STATUS_CHANGED",
        actor=user,
        customer_id=incident.customer_id,
        details={"incident_id": str(incident.id), "from": previous, "to": status},
    )
    return True


def add_note(db, incident, user, body: str, kind: str, occurred_at: datetime | None) -> IncidentNote:
    """Operator note on the timeline (audited). Caller commits."""
    text = (body or "").strip()
    if not text:
        raise IncidentInputError("note_required", "Il testo della nota è obbligatorio.")
    now = utcnow()
    when = occurred_at or now
    if when > now + FUTURE_TOLERANCE:
        raise IncidentInputError("note_in_future", "La nota non può essere nel futuro.")
    note = IncidentNote(
        id=uuid.uuid4(),
        incident_id=incident.id,
        kind=kind if kind in NOTE_KINDS else "note",
        body=text[:4000],
        occurred_at=when,
        author_user_id=user.id,
        created_at=now,
    )
    db.add(note)
    incident.updated_at = now
    core.add_event(
        db,
        "INCIDENT_NOTE_ADDED",
        actor=user,
        customer_id=incident.customer_id,
        details={"incident_id": str(incident.id), "kind": kind},
    )
    return note


def set_devices(db, incident, user, wanted: set) -> None:
    """Replace the involved devices (audited); they must belong to the incident's customer. Caller commits."""
    wanted = set(wanted) - {None}
    valid = set(db.scalars(select(Device.id).where(Device.id.in_(wanted), Device.customer_id == incident.customer_id))) if wanted else set()
    if valid != wanted:
        raise IncidentInputError("foreign_devices", "Gli apparati devono appartenere al cliente dell'incidente.")
    current = {link.device_id: link for link in db.scalars(select(IncidentDevice).where(IncidentDevice.incident_id == incident.id))}
    now = utcnow()
    for device_id in wanted - set(current):
        db.add(IncidentDevice(incident_id=incident.id, device_id=device_id, added_at=now))
    for device_id in set(current) - wanted:
        db.delete(current[device_id])
    incident.updated_at = now
    core.add_event(
        db,
        "INCIDENT_DEVICES_CHANGED",
        actor=user,
        customer_id=incident.customer_id,
        details={"incident_id": str(incident.id), "devices": len(wanted)},
    )


def add_hypothesis(db, incident, user, statement: str, category: str, candidate_key: str) -> IncidentHypothesis:
    """Record a possible cause, typed by the operator or taken from a suggestion (audited). Caller commits."""
    now = utcnow()
    if candidate_key:
        # Re-derive the candidate server-side: the client only names it.
        devices = _incident_devices(db, incident.id)
        timeline = build_timeline(db, incident, [device.id for device in devices], now)
        candidate = next((c for c in suggest(incident, timeline) if c.key == candidate_key), None)
        if candidate is None:
            raise IncidentInputError("candidate_gone", "La correlazione suggerita non è più disponibile.", title="Ipotesi non registrata")
        hypothesis = IncidentHypothesis(
            id=uuid.uuid4(),
            incident_id=incident.id,
            statement=candidate.statement,
            category=candidate.category,
            origin="suggested",
            confidence=candidate.confidence,
            evidence=candidate.evidence(),
            created_by_user_id=user.id,
            created_at=now,
        )
    else:
        text = (statement or "").strip()
        if not text:
            raise IncidentInputError("statement_required", "Descrivi l'ipotesi.")
        hypothesis = IncidentHypothesis(
            id=uuid.uuid4(),
            incident_id=incident.id,
            statement=text[:2000],
            category=category if category in ROOT_CAUSE_CATEGORIES else "other",
            origin="operator",
            evidence=[],
            created_by_user_id=user.id,
            created_at=now,
        )
    db.add(hypothesis)
    incident.updated_at = now
    core.add_event(
        db,
        "INCIDENT_HYPOTHESIS_ADDED",
        actor=user,
        customer_id=incident.customer_id,
        details={"incident_id": str(incident.id), "origin": hypothesis.origin, "category": hypothesis.category},
    )
    return hypothesis


def decide_hypothesis(db, incident, hypothesis, user, decision: str, note: str) -> str:
    """Confirm, reject or reopen a hypothesis (audited); returns the classic confirmation message. Caller commits."""
    note = (note or "").strip()
    if decision in ("confirm", "reject") and not note:
        raise IncidentInputError("decision_note_required", "Indica su quali evidenze si basa la decisione.", title="Nota obbligatoria")
    now = utcnow()
    if decision == "confirm":
        for other in db.scalars(
            select(IncidentHypothesis).where(
                IncidentHypothesis.incident_id == incident.id,
                IncidentHypothesis.status == "confirmed",
                IncidentHypothesis.id != hypothesis.id,
            )
        ):
            other.status = "proposed"
            other.decision_note = f"Sostituita da un'altra causa confermata il {now.date().isoformat()}."
        hypothesis.status = "confirmed"
        incident.root_cause_hypothesis_id = hypothesis.id
        event, message = "INCIDENT_ROOT_CAUSE_CONFIRMED", "Causa radice confermata."
    elif decision == "reject":
        hypothesis.status = "rejected"
        if incident.root_cause_hypothesis_id == hypothesis.id:
            incident.root_cause_hypothesis_id = None
        event, message = "INCIDENT_HYPOTHESIS_REJECTED", "Ipotesi scartata."
    elif decision == "reopen":
        hypothesis.status = "proposed"
        if incident.root_cause_hypothesis_id == hypothesis.id:
            incident.root_cause_hypothesis_id = None
        event, message = "INCIDENT_HYPOTHESIS_REOPENED", "Ipotesi di nuovo da verificare."
    else:
        raise IncidentInputError("invalid_decision", "Decisione non valida.")
    hypothesis.decided_by_user_id = user.id
    hypothesis.decided_at = now
    if note:
        hypothesis.decision_note = note[:2000]
    incident.updated_at = now
    core.add_event(
        db,
        event,
        actor=user,
        customer_id=incident.customer_id,
        details={
            "incident_id": str(incident.id),
            "hypothesis_id": str(hypothesis.id),
            "category": hypothesis.category,
            "origin": hypothesis.origin,
        },
    )
    return message


def _hypotheses(db, incident_id):
    author = User.__table__.alias("author")
    decider = User.__table__.alias("decider")
    rows = db.execute(
        select(IncidentHypothesis, author.c.username, decider.c.username)
        .outerjoin(author, author.c.id == IncidentHypothesis.created_by_user_id)
        .outerjoin(decider, decider.c.id == IncidentHypothesis.decided_by_user_id)
        .where(IncidentHypothesis.incident_id == incident_id)
        .order_by(IncidentHypothesis.created_at, IncidentHypothesis.id)
    ).all()
    return [(hypothesis, author_name or "", decider_name or "") for hypothesis, author_name, decider_name in rows]


