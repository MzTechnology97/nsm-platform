"""Syslog storage (LOG-01): device log lines and sources not matched to a device."""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, SmallInteger, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class DeviceLogEntry(Base):
    __tablename__ = "device_log_entries"
    __table_args__ = (
        Index("ix_device_log_entries_device_id_id", "device_id", "id"),
        Index("ix_device_log_entries_received_at", "received_at"),
        Index("ix_device_log_entries_severity_id", "severity", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("devices.id", ondelete="CASCADE"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    source_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    facility: Mapped[int | None] = mapped_column(SmallInteger)
    severity: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=6)
    hostname: Mapped[str | None] = mapped_column(String(255))
    program: Mapped[str | None] = mapped_column(String(100))
    topics: Mapped[str | None] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Set by the security rules (LOG-01 step 3): login_failure, login_success, ...
    category: Mapped[str | None] = mapped_column(String(40))


class SyslogUnknownSource(Base):
    """Senders that match no device: shown to the admin, their lines are not stored."""

    __tablename__ = "syslog_unknown_sources"

    source_ip: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    messages: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    sample: Mapped[str | None] = mapped_column(String(300))
