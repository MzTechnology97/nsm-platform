import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class BackupPolicySettings(Base):
    __tablename__ = "backup_policy_settings"

    policy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("backup_policies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    description: Mapped[str | None] = mapped_column(Text)
    schedule_kind: Mapped[str] = mapped_column(String(30), default="daily")
    schedule_time: Mapped[str] = mapped_column(String(5), default="03:00")
    schedule_weekday: Mapped[int | None] = mapped_column(Integer)
    schedule_monthday: Mapped[int | None] = mapped_column(Integer)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BackupArtifact(Base):
    __tablename__ = "backup_artifacts"
    __table_args__ = (
        Index("ix_backup_artifacts_run_id", "run_id"),
        Index("ix_backup_artifacts_created_at", "created_at"),
        Index("ix_backup_artifacts_deleted_at", "deleted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("backup_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_type: Mapped[str] = mapped_column(String(60))
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(1000))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="SET NULL"),
    )
