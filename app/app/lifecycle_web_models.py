"""Lifecycle looked up on the web: one row per inventory model and what the sources said about it."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LifecycleWebLookup(Base):
    __tablename__ = "lifecycle_web_lookups"
    __table_args__ = (UniqueConstraint("vendor", "model_key", name="uq_lifecycle_web_lookup_model"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    vendor: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(150))       # as the inventory reports it
    model_key: Mapped[str] = mapped_column(String(150))   # lifecycle_catalog.model_key
    query: Mapped[str | None] = mapped_column(String(150))  # another name to search with (SKU), set by an operator
    provider: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))       # found, not_found, error
    candidates: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(255))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    applied_record_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("lifecycle_records.id", ondelete="SET NULL"))
    applied_product: Mapped[str | None] = mapped_column(String(150))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
