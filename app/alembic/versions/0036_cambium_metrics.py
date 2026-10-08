"""cnMaestro metrics history for Cambium devices (VEND-02).

Revision ID: 0036_cambium_metrics
Revises: 0035_vendor_firmware
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0036_cambium_metrics"
down_revision: Union[str, None] = "0035_vendor_firmware"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "cambium_metric_samples",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_dbm", sa.Float(), nullable=True),
        sa.Column("snr_db", sa.Float(), nullable=True),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("ram_percent", sa.Float(), nullable=True),
        sa.Column("stations", sa.Integer(), nullable=True),
        sa.Column("uptime_seconds", sa.BigInteger(), nullable=True),
        sa.Column("dl_throughput_bps", sa.BigInteger(), nullable=True),
        sa.Column("ul_throughput_bps", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_cambium_metric_samples_device_observed", "cambium_metric_samples", ["device_id", "observed_at"])


def downgrade():
    op.drop_index("ix_cambium_metric_samples_device_observed", table_name="cambium_metric_samples")
    op.drop_table("cambium_metric_samples")
