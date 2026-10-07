"""Incident model (INC-01).

An Incident groups what an operator is investigating for one Customer: the
Devices involved, a status, and operator notes. Facts are never stored here:
the timeline is rebuilt from the records NSM already keeps (see
``incident_timeline``), so it always reflects the evidence as recorded.
"""
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow

INCIDENT_STATUSES = {
    "open": "Aperto",
    "investigating": "In analisi",
    "monitoring": "In monitoraggio",
    "resolved": "Risolto",
}
INCIDENT_SEVERITIES = {"critical": "Critico", "high": "Alto", "medium": "Medio", "low": "Basso"}
NOTE_KINDS = {"note": "Nota", "action": "Azione eseguita"}
ROOT_CAUSE_CATEGORIES = {
    "configuration": "Configurazione",
    "firmware": "Firmware / aggiornamento",
    "hardware": "Guasto hardware",
    "power": "Alimentazione",
    "connectivity": "Connettività / link",
    "upstream": "Rete a monte / provider",
    "security": "Sicurezza",
    "human": "Intervento manuale",
    "other": "Altro",
}
HYPOTHESIS_STATUSES = {"proposed": "Da verificare", "confirmed": "Confermata", "rejected": "Scartata"}


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_customer_status", "customer_id", "status"),
        Index("ix_incidents_started_at", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    # INC-03: only an operator-confirmed hypothesis becomes the root cause.
    root_cause_hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("incident_hypotheses.id", ondelete="SET NULL", use_alter=True)
    )


class IncidentHypothesis(Base):
    """A possible cause. Suggested ones come from heuristics and stay proposals until confirmed."""

    __tablename__ = "incident_hypotheses"
    __table_args__ = (Index("ix_incident_hypotheses_incident", "incident_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(30), default="other", nullable=False)
    origin: Mapped[str] = mapped_column(String(20), default="operator", nullable=False)
    confidence: Mapped[str | None] = mapped_column(String(20))
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="proposed", nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)


class IncidentDevice(Base):
    __tablename__ = "incident_devices"
    __table_args__ = (UniqueConstraint("incident_id", "device_id", name="uq_incident_devices"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class IncidentNote(Base):
    """Operator statement on the timeline; always shown as such, never as an observed fact."""

    __tablename__ = "incident_notes"
    __table_args__ = (Index("ix_incident_notes_incident", "incident_id", "occurred_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="note", nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
