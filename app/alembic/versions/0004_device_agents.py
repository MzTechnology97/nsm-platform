"""Persistent outbound device-agent credentials.

Revision ID: 0004_device_agents
Revises: 0003_ui_branding
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_device_agents"
down_revision: Union[str, None] = "0003_ui_branding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "device_agent_credentials",
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("agent_type", sa.String(40), nullable=False, server_default="mikrotik"),
        sa.Column("agent_version", sa.String(40), nullable=False, server_default="0.1"),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("heartbeat_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_rotated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_source_ip", sa.String(64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_index(
        "ix_device_agent_credentials_last_heartbeat_at",
        "device_agent_credentials",
        ["last_heartbeat_at"],
    )


def downgrade():
    op.drop_index(
        "ix_device_agent_credentials_last_heartbeat_at",
        table_name="device_agent_credentials",
    )
    op.drop_table("device_agent_credentials")
