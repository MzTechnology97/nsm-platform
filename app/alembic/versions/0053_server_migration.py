"""Moving NSM to a new server address: migrations and per-device items.

Revision ID: 0053_server_migration
Revises: 0050_ispbilling
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0053_server_migration"
down_revision: Union[str, None] = "0050_ispbilling"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "server_migrations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=150)),
        sa.Column("agent_base_url", sa.String(length=255), nullable=False),
        sa.Column("acs_url", sa.String(length=255)),
        sa.Column("syslog_ip", sa.String(length=45)),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("target_check", sa.JSON()),
    )
    op.create_index("ix_server_migrations_created", "server_migrations", ["created_at"])
    op.create_table(
        "server_migration_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("migration_id", sa.Uuid(), sa.ForeignKey("server_migrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("job_id", sa.Uuid()),
        sa.Column("detail", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("arrived_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_server_migration_items_migration", "server_migration_items", ["migration_id", "device_id"])


def downgrade():
    op.drop_index("ix_server_migration_items_migration", table_name="server_migration_items")
    op.drop_table("server_migration_items")
    op.drop_index("ix_server_migrations_created", table_name="server_migrations")
    op.drop_table("server_migrations")
