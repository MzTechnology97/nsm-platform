"""Integrated syslog receiver storage (LOG-01).

Revision ID: 0030_syslog
Revises: 0029_interface_traffic
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0030_syslog"
down_revision: Union[str, None] = "0029_interface_traffic"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "device_log_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ip", sa.String(64), nullable=False),
        sa.Column("facility", sa.SmallInteger(), nullable=True),
        sa.Column("severity", sa.SmallInteger(), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=True),
        sa.Column("program", sa.String(100), nullable=True),
        sa.Column("topics", sa.String(200), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("category", sa.String(40), nullable=True),
    )
    op.create_index("ix_device_log_entries_device_id_id", "device_log_entries", ["device_id", "id"])
    op.create_index("ix_device_log_entries_received_at", "device_log_entries", ["received_at"])
    op.create_index("ix_device_log_entries_severity_id", "device_log_entries", ["severity", "id"])
    op.create_table(
        "syslog_unknown_sources",
        sa.Column("source_ip", sa.String(64), primary_key=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("messages", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sample", sa.String(300), nullable=True),
    )


def downgrade():
    op.drop_table("syslog_unknown_sources")
    op.drop_index("ix_device_log_entries_severity_id", table_name="device_log_entries")
    op.drop_index("ix_device_log_entries_received_at", table_name="device_log_entries")
    op.drop_index("ix_device_log_entries_device_id_id", table_name="device_log_entries")
    op.drop_table("device_log_entries")
