"""ACS monitoring: metric samples of TR-069 CPEs read from GenieACS.

Revision ID: 0052_acs_metric_samples
Revises: 0051_ispbilling_radius
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0052_acs_metric_samples"
down_revision: Union[str, None] = "0051_ispbilling_radius"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "acs_metric_samples",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uptime_seconds", sa.BigInteger()),
        sa.Column("cpu_percent", sa.Float()),
        sa.Column("ram_percent", sa.Float()),
        sa.Column("wan_rx_bps", sa.BigInteger()),
        sa.Column("wan_tx_bps", sa.BigInteger()),
        sa.Column("wifi_clients", sa.Integer()),
        sa.Column("lan_hosts", sa.Integer()),
        sa.Column("dsl_down_bps", sa.BigInteger()),
        sa.Column("dsl_up_bps", sa.BigInteger()),
        sa.Column("dsl_snr_down_db", sa.Float()),
        sa.Column("dsl_attenuation_down_db", sa.Float()),
    )
    op.create_index("ix_acs_metric_samples_device_observed", "acs_metric_samples", ["device_id", "observed_at"])


def downgrade():
    op.drop_index("ix_acs_metric_samples_device_observed", table_name="acs_metric_samples")
    op.drop_table("acs_metric_samples")
