import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, LargeBinary, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow

FREQUENCY_LABELS = {"monthly": "Mensile", "quarterly": "Trimestrale", "annual": "Annuale"}


class GeneratedReport(Base):
    """Immutable archived report output with its SHA-256 evidence (REP-01).

    The rendered bytes are stored in PostgreSQL so archived reports are part of
    the regular logical database backup and need no extra runtime volume.
    """

    __tablename__ = "generated_reports"
    __table_args__ = (
        Index("ix_generated_reports_generated_at", "generated_at"),
        Index("ix_generated_reports_customer_id", "customer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_type: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="SET NULL")
    )
    scope_label: Mapped[str] = mapped_column(String(200), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    output_format: Mapped[str] = mapped_column(String(10), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(80), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    generated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("report_schedules.id", ondelete="SET NULL")
    )


class ReportSchedule(Base):
    """Recurring report generation for a closed calendar period (REP-03).

    ``last_period_end`` is the idempotency marker: a period is generated at most
    once per schedule, however many worker ticks or retries occur.
    """

    __tablename__ = "report_schedules"
    __table_args__ = (Index("ix_report_schedules_enabled", "is_enabled"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="CASCADE")
    )
    output_format: Mapped[str] = mapped_column(String(10), nullable=False)
    frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    last_period_end: Mapped[date | None] = mapped_column(Date)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(20))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_report_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
