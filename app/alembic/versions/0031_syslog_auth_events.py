"""Access events recognised in syslog lines (LOG-01 step 3).

Revision ID: 0031_syslog_auth_events
Revises: 0030_syslog
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0031_syslog_auth_events"
down_revision: Union[str, None] = "0030_syslog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "device_auth_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("username", sa.String(100), nullable=True),
        sa.Column("remote_ip", sa.String(64), nullable=True),
        sa.Column("service", sa.String(40), nullable=True),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("evaluated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_device_auth_events_device_occurred", "device_auth_events", ["device_id", "occurred_at"])
    op.create_index("ix_device_auth_events_remote_occurred", "device_auth_events", ["remote_ip", "occurred_at"])
    op.create_index("ix_device_auth_events_evaluated", "device_auth_events", ["evaluated"])


def downgrade():
    op.drop_index("ix_device_auth_events_evaluated", table_name="device_auth_events")
    op.drop_index("ix_device_auth_events_remote_occurred", table_name="device_auth_events")
    op.drop_index("ix_device_auth_events_device_occurred", table_name="device_auth_events")
    op.drop_table("device_auth_events")
