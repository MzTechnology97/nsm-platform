"""MikroTik agent credentials and job queue.

Revision ID: 0005_mikrotik_agent
Revises: 0004_core06_backups
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_mikrotik_agent"
down_revision: Union[str, None] = "0004_core06_backups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "device_agent_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("agent_type", sa.String(60), nullable=False, server_default="mikrotik_agent"),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "agent_type", name="uq_device_agent_credentials_device_agent"),
    )
    op.create_index("ix_device_agent_credentials_device_id", "device_agent_credentials", ["device_id"])
    op.create_index("ix_device_agent_credentials_secret_hash", "device_agent_credentials", ["secret_hash"])

    op.create_table(
        "device_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("result", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_device_jobs_device_status", "device_jobs", ["device_id", "status"])
    op.create_index("ix_device_jobs_created_at", "device_jobs", ["created_at"])
    op.create_index("ix_device_jobs_expires_at", "device_jobs", ["expires_at"])


def downgrade():
    op.drop_index("ix_device_jobs_expires_at", table_name="device_jobs")
    op.drop_index("ix_device_jobs_created_at", table_name="device_jobs")
    op.drop_index("ix_device_jobs_device_status", table_name="device_jobs")
    op.drop_table("device_jobs")
    op.drop_index("ix_device_agent_credentials_secret_hash", table_name="device_agent_credentials")
    op.drop_index("ix_device_agent_credentials_device_id", table_name="device_agent_credentials")
    op.drop_table("device_agent_credentials")
