"""Moving NSM to a new server address: the migration and what happened to each device.

The old server tells its devices the new address — agents receive a script pointing at the new URL,
TR-069 CPEs a new ACS URL — after the new server was restored from this database. The token proves
that the target is such a restore: only its hash is stored in clear, the token itself is encrypted
with the platform key (restored together with the database).
"""
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ServerMigration(Base):
    __tablename__ = "server_migrations"
    __table_args__ = (Index("ix_server_migrations_created", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(150))
    agent_base_url: Mapped[str] = mapped_column(String(255))   # what the MikroTik agents will call
    acs_url: Mapped[str | None] = mapped_column(String(255))    # what the TR-069 CPEs will inform
    syslog_ip: Mapped[str | None] = mapped_column(String(45))   # where RouterOS sends syslog (IPv4)
    token_hash: Mapped[str] = mapped_column(String(64))
    token_encrypted: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft, running, cancelled
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_check: Mapped[dict | None] = mapped_column(JSON)


class ServerMigrationItem(Base):
    __tablename__ = "server_migration_items"
    __table_args__ = (Index("ix_server_migration_items_migration", "migration_id", "device_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    migration_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("server_migrations.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("devices.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))      # agent | tr069
    status: Mapped[str] = mapped_column(String(16))    # queued, sent, failed, arrived, reinstall
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    detail: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
