"""ICMP latency and loss measured from the NSM server (MON-01).

Revision ID: 0039_device_ping_samples
Revises: 0038_interface_errors
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0039_device_ping_samples"
down_revision: Union[str, None] = "0038_interface_errors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "device_ping_samples",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target", sa.String(64), nullable=False),
        sa.Column("sent", sa.Integer(), nullable=False),
        sa.Column("received", sa.Integer(), nullable=False),
        sa.Column("rtt_min", sa.Float(), nullable=True),
        sa.Column("rtt_avg", sa.Float(), nullable=True),
        sa.Column("rtt_max", sa.Float(), nullable=True),
    )
    op.create_index("ix_device_ping_samples_device_observed", "device_ping_samples", ["device_id", "observed_at"])
    op.create_index("ix_device_ping_samples_observed_at", "device_ping_samples", ["observed_at"])


def downgrade():
    op.drop_index("ix_device_ping_samples_observed_at", table_name="device_ping_samples")
    op.drop_index("ix_device_ping_samples_device_observed", table_name="device_ping_samples")
    op.drop_table("device_ping_samples")
