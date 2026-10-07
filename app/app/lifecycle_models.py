"""Vendor lifecycle catalog (LIFE-01): one record per vendor model with its source."""
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow

# How a Device got (or did not get) its lifecycle data.
MATCH_LABELS = {
    "catalog": "Da catalogo",
    "manual": "Impostato manualmente",
    "ambiguous": "Corrispondenza ambigua",
    "no_record": "Modello non in catalogo",
    "no_model": "Modello non rilevato",
}


class LifecycleRecord(Base):
    """EOL (end of sale/life) and EOS (end of support) dates of one vendor model.

    A record without dates states that the vendor still supported the model
    when the source was checked (`evidence_date`).
    """

    __tablename__ = "lifecycle_records"
    __table_args__ = (UniqueConstraint("vendor", "model_key", name="uq_lifecycle_records_vendor_model"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    vendor: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(150), nullable=False)
    model_key: Mapped[str] = mapped_column(String(150), nullable=False)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    eol_date: Mapped[date | None] = mapped_column(Date)
    eos_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(160), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500))
    evidence_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
