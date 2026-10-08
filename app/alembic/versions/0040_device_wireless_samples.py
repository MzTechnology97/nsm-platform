"""Wireless signal per MikroTik radio interface (MON-01).

Revision ID: 0040_device_wireless_samples
Revises: 0039_device_ping_samples
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0040_device_wireless_samples"
down_revision: Union[str, None] = "0039_device_ping_samples"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "device_wireless_samples",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("interface", sa.String(100), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clients", sa.Integer(), nullable=False),
        sa.Column("signal_min", sa.Float(), nullable=True),
        sa.Column("signal_avg", sa.Float(), nullable=True),
        sa.Column("signal_max", sa.Float(), nullable=True),
        sa.Column("ccq_avg", sa.Float(), nullable=True),
    )
    op.create_index("ix_device_wireless_samples_device_iface_observed", "device_wireless_samples", ["device_id", "interface", "observed_at"])
    op.create_index("ix_device_wireless_samples_observed_at", "device_wireless_samples", ["observed_at"])


def downgrade():
    op.drop_index("ix_device_wireless_samples_observed_at", table_name="device_wireless_samples")
    op.drop_index("ix_device_wireless_samples_device_iface_observed", table_name="device_wireless_samples")
    op.drop_table("device_wireless_samples")
