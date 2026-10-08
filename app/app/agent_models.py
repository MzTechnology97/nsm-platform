import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class DeviceAgentCredential(Base):
    __tablename__ = "device_agent_credentials"
    __table_args__ = (
        UniqueConstraint("device_id", "agent_type", name="uq_device_agent_credentials_device_agent"),
        Index("ix_device_agent_credentials_device_id", "device_id"),
        Index("ix_device_agent_credentials_secret_hash", "secret_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_type: Mapped[str] = mapped_column(String(60), default="mikrotik_agent")
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceJob(Base):
    __tablename__ = "device_jobs"
    __table_args__ = (
        Index("ix_device_jobs_device_status", "device_id", "status"),
        Index("ix_device_jobs_created_at", "created_at"),
        Index("ix_device_jobs_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)


class DeviceMetricSample(Base):
    __tablename__ = "device_metric_samples"
    __table_args__ = (
        Index("ix_device_metric_samples_device_observed", "device_id", "observed_at"),
        Index("ix_device_metric_samples_observed_at", "observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    cpu_load: Mapped[float | None] = mapped_column(Float)
    free_memory_bytes: Mapped[int | None] = mapped_column(BigInteger)
    total_memory_bytes: Mapped[int | None] = mapped_column(BigInteger)
    uptime_text: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(40), default="mikrotik_agent", nullable=False)


class DeviceInterfaceSample(Base):
    """Interface traffic history (MON-01): bit/s computed from the agent byte counters."""
    __tablename__ = "device_interface_samples"
    __table_args__ = (
        Index("ix_device_interface_samples_device_iface_observed", "device_id", "interface", "observed_at"),
        Index("ix_device_interface_samples_observed_at", "observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    interface: Mapped[str] = mapped_column(String(100), nullable=False)
    if_type: Mapped[str | None] = mapped_column(String(40))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    rx_bytes: Mapped[int | None] = mapped_column(BigInteger)
    tx_bytes: Mapped[int | None] = mapped_column(BigInteger)
    rx_bps: Mapped[float | None] = mapped_column(Float)
    tx_bps: Mapped[float | None] = mapped_column(Float)
    # Errors and drops in the interval since the previous sample (Agent 0.49.20+; None before).
    rx_errors: Mapped[int | None] = mapped_column(BigInteger)
    tx_errors: Mapped[int | None] = mapped_column(BigInteger)
    rx_drops: Mapped[int | None] = mapped_column(BigInteger)
    tx_drops: Mapped[int | None] = mapped_column(BigInteger)
