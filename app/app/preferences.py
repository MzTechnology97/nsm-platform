from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[object] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    theme: Mapped[str] = mapped_column(String(20), default="light", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class PlatformBranding(Base):
    __tablename__ = "platform_branding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    portal_name: Mapped[str] = mapped_column(
        String(160), default="Network Security Platform", nullable=False
    )
    tagline: Mapped[str | None] = mapped_column(String(240))
    primary_color: Mapped[str] = mapped_column(
        String(7), default="#1f5f8b", nullable=False
    )
    sidebar_color: Mapped[str] = mapped_column(
        String(7), default="#111827", nullable=False
    )
    highlight_color: Mapped[str] = mapped_column(
        String(7), default="#f59e0b", nullable=False
    )
    logo_filename: Mapped[str | None] = mapped_column(String(255))
    logo_content_type: Mapped[str | None] = mapped_column(String(80))
    logo_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
