import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class VendorFirmwareRelease(Base):
    """A firmware release of a non-MikroTik manufacturer (VEND-01).

    ``source``: ``uisp`` (latest version reported by the UISP console for a model),
    ``vendor_api`` (manufacturer's official update API) or ``operator`` (entered
    from the manufacturer's official download page).
    """

    __tablename__ = "vendor_firmware_releases"
    __table_args__ = (
        UniqueConstraint("brand", "model_pattern", "platform", "version", "source", name="uq_vendor_firmware_release"),
        Index("ix_vendor_firmware_releases_brand", "brand"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    brand: Mapped[str] = mapped_column(String(40))
    model_pattern: Mapped[str] = mapped_column(String(120), default="")
    platform: Mapped[str] = mapped_column(String(40), default="")
    channel: Mapped[str] = mapped_column(String(30), default="stable")
    version: Mapped[str] = mapped_column(String(80))
    released_at: Mapped[date | None] = mapped_column(Date)
    notes_url: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    security: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
