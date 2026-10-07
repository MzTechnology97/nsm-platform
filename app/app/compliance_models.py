"""Compliance baselines and evaluated results (COMP-01 / COMP-02)."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow

SCOPE_LABELS = {
    "global": "Globale",
    "vendor": "Vendor",
    "customer": "Cliente",
    "site": "Sede",
    "device": "Apparato",
}
# More specific scopes override the controls they define.
SCOPE_PRIORITY = {"global": 0, "vendor": 1, "customer": 2, "site": 3, "device": 4}
RESULT_LABELS = {
    "pass": "Conforme",
    "fail": "Non conforme",
    "unknown": "Nessuna evidenza",
    "not_applicable": "Non applicabile",
}


class ComplianceBaseline(Base):
    """A set of control settings for one scope; versioned on every change."""

    __tablename__ = "compliance_baselines"
    __table_args__ = (Index("ix_compliance_baselines_scope", "scope_type", "is_enabled"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    scope_type: Mapped[str] = mapped_column(String(20), default="global", nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(60))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # {control_id: {"enabled": bool, "params": {...}}}; controls not listed are inherited.
    controls: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ComplianceResult(Base):
    """Latest evaluation of one control on one Device."""

    __tablename__ = "compliance_results"
    __table_args__ = (
        UniqueConstraint("device_id", "control_id", name="uq_compliance_results_device_control"),
        Index("ix_compliance_results_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    control_id: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence: Mapped[str] = mapped_column(Text, default="", nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    baseline_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("compliance_baselines.id", ondelete="SET NULL"))
    baseline_version: Mapped[int | None] = mapped_column(Integer)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # COMP-03: handling of a non-compliance (the evaluated status is never overwritten).
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exception_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exception_reason: Mapped[str | None] = mapped_column(Text)


class ComplianceResultHistory(Base):
    """Status changes and operator decisions on one result, kept after resolution."""

    __tablename__ = "compliance_result_history"
    __table_args__ = (Index("ix_compliance_result_history_result", "result_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("compliance_results.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
