"""Bulk migration of customer CPEs to the self-hosted ACS (ACS-02 bulk onboarding).

An operator who already runs a fleet of CPEs on a vendor cloud ACS (e.g. TP-Link TAUC) points the
fleet at the NSM GenieACS by reconfiguring CWMP over the operator's own management network. A run
records what it did per host; credentials are never stored here (held only in memory for the run).
"""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Run lifecycle and per-host outcomes (stored as short codes, translated in the UI).
RUN_STATES = ("pending", "running", "done", "failed", "cancelled")
HOST_STATES = (
    "pending",        # queued, not processed yet
    "unreachable",    # management port closed or no answer (no credential ever sent)
    "not_cpe",        # answered but not the expected vendor CPE
    "auth_failed",    # the CPE answered but refused the provided credential
    "already_target",  # CWMP already points at the target ACS
    "configured",     # CWMP set to the target ACS (or would be, in a dry run)
    "error",          # the CPE was reached and authenticated but the change failed
    "skipped",        # excluded (e.g. the host is the ACS/NSM server itself)
)


class AcsMigrationRun(Base):
    """One bulk migration pass over a management subnet. No credentials or per-host secrets are stored."""

    __tablename__ = "acs_migration_runs"
    __table_args__ = (Index("ix_acs_migration_runs_created", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(150))  # username, for the audit trail
    vendor: Mapped[str] = mapped_column(String(40))
    subnet: Mapped[str] = mapped_column(String(64))  # CIDR of the operator's management network
    port: Mapped[int] = mapped_column(Integer)
    target_acs_url: Mapped[str] = mapped_column(String(255))
    inform_interval: Mapped[int] = mapped_column(Integer)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    total_hosts: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    reachable: Mapped[int] = mapped_column(Integer, default=0)
    configured: Mapped[int] = mapped_column(Integer, default=0)
    already_target: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)


class AcsMigrationHost(Base):
    """Result for one address in a run."""

    __tablename__ = "acs_migration_hosts"
    __table_args__ = (Index("ix_acs_migration_hosts_run", "run_id", "address"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("acs_migration_runs.id", ondelete="CASCADE"))
    address: Mapped[str] = mapped_column(String(45))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    detail: Mapped[str | None] = mapped_column(String(255))
    model: Mapped[str | None] = mapped_column(String(120))
    firmware: Mapped[str | None] = mapped_column(String(150))
    previous_acs_url: Mapped[str | None] = mapped_column(String(255))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
