import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class BackupRestoreTest(Base):
    """Operator-recorded evidence that a stored backup artifact restores correctly.

    NSM never restores a production Device automatically.  The operator restores
    the artifact in a controlled target (lab, spare device, configuration review)
    and records the outcome here.  Artifact identity is copied onto the record so
    the evidence survives retention or deletion of the artifact itself.
    """

    __tablename__ = "backup_restore_tests"
    __table_args__ = (
        Index("ix_backup_restore_tests_device_id", "device_id"),
        Index("ix_backup_restore_tests_artifact_id", "artifact_id"),
        Index("ix_backup_restore_tests_performed_at", "performed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("backup_artifacts.id", ondelete="SET NULL")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("backup_runs.id", ondelete="SET NULL")
    )
    artifact_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(60), nullable=False)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source_routeros_version: Mapped[str | None] = mapped_column(String(80))
    method: Mapped[str] = mapped_column(String(40), nullable=False)
    target_label: Mapped[str] = mapped_column(String(200), nullable=False)
    target_routeros_version: Mapped[str | None] = mapped_column(String(80))
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    integrity: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    performed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
