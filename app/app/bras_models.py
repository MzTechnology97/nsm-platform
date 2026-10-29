import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import utcnow


class BrasSession(Base):
    """The PPPoE session of one username on the MikroTik BRAS, as last read (BRAS-01): online now, or since when offline."""

    __tablename__ = "bras_sessions"
    __table_args__ = (Index("ux_bras_sessions_username", "username", unique=True), Index("ix_bras_sessions_mac", "mac"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(120))  # lower case
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    service: Mapped[str | None] = mapped_column(String(20))
    caller_id: Mapped[str | None] = mapped_column(String(64))
    mac: Mapped[str | None] = mapped_column(String(17))  # caller-id when it is a MAC, upper case with colons
    address: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(40))
    uptime_seconds: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    offline_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IpAssignment(Base):
    """Who held a public address and when (CYBER-01): one row per PPPoE session and address seen on the BRAS, from the
    session start to the read that found it gone (``ended_at`` empty while it is still online)."""

    __tablename__ = "ip_assignments"
    __table_args__ = (Index("ix_ip_assignments_address", "address", "started_at"), Index("ix_ip_assignments_open", "username", "ended_at"))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    address: Mapped[str] = mapped_column(String(64))
    username: Mapped[str] = mapped_column(String(120))  # lower case, as in bras_sessions
    caller_id: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(20), default="bras")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
