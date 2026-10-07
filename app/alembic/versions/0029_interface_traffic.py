"""Interface traffic samples for the Cacti-style graphs (MON-01).

Revision ID: 0029_interface_traffic
Revises: 0028_notification_digest
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0029_interface_traffic"
down_revision: Union[str, None] = "0028_notification_digest"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "device_interface_samples",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("interface", sa.String(100), nullable=False),
        sa.Column("if_type", sa.String(40), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rx_bytes", sa.BigInteger(), nullable=True),
        sa.Column("tx_bytes", sa.BigInteger(), nullable=True),
        sa.Column("rx_bps", sa.Float(), nullable=True),
        sa.Column("tx_bps", sa.Float(), nullable=True),
    )
    op.create_index("ix_device_interface_samples_device_iface_observed", "device_interface_samples", ["device_id", "interface", "observed_at"])
    op.create_index("ix_device_interface_samples_observed_at", "device_interface_samples", ["observed_at"])


def downgrade():
    op.drop_index("ix_device_interface_samples_observed_at", table_name="device_interface_samples")
    op.drop_index("ix_device_interface_samples_device_iface_observed", table_name="device_interface_samples")
    op.drop_table("device_interface_samples")
