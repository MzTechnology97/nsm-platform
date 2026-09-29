import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class MikrotikRestorePlan(Base):
    __tablename__ = "mikrotik_restore_plans"
    __table_args__ = (
        Index("ix_mikrotik_restore_plans_device_id", "device_id"),
        Index("ix_mikrotik_restore_plans_backup_run_id", "backup_run_id"),
        Index("ix_mikrotik_restore_plans_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    backup_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("backup_runs.id", ondelete="CASCADE"), nullable=False
    )
    restore_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    readiness: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
