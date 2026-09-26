import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class DeviceAgentCredential(Base):
    __tablename__ = "device_agent_credentials"

    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    agent_type: Mapped[str] = mapped_column(String(40), default="mikrotik")
    agent_version: Mapped[str] = mapped_column(String(40), default="0.1")
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    heartbeat_interval_seconds: Mapped[int] = mapped_column(Integer, default=300)
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_source_ip: Mapped[str | None] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
