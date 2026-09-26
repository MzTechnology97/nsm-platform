import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class MikrotikBackupJobSecret(Base):
    __tablename__ = "mikrotik_backup_job_secrets"

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("device_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    encrypted_backup_password: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class BackupUploadSession(Base):
    __tablename__ = "backup_upload_sessions"
    __table_args__ = (
        UniqueConstraint("job_id", "artifact_type", name="uq_backup_upload_job_artifact"),
        Index("ix_backup_upload_sessions_device_id", "device_id"),
        Index("ix_backup_upload_sessions_status", "status"),
        Index("ix_backup_upload_sessions_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("device_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_type: Mapped[str] = mapped_column(String(60), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    temp_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    expected_size: Mapped[int | None] = mapped_column(BigInteger)
    received_size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="receiving", nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
