"""UISP operational metric history (UBNT-02).

Revision ID: 0022_uisp_metric_samples
Revises: 0021_lifecycle_remediation
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0022_uisp_metric_samples"
down_revision: Union[str, None] = "0021_lifecycle_remediation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "uisp_metric_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("ram_percent", sa.Float(), nullable=True),
        sa.Column("signal_dbm", sa.Float(), nullable=True),
        sa.Column("downlink_capacity_bps", sa.BigInteger(), nullable=True),
        sa.Column("uplink_capacity_bps", sa.BigInteger(), nullable=True),
        sa.Column("uptime_seconds", sa.BigInteger(), nullable=True),
        sa.Column("frequency_mhz", sa.Float(), nullable=True),
        sa.Column("stations", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_uisp_metric_samples_device_observed", "uisp_metric_samples", ["device_id", "observed_at"])


def downgrade():
    op.drop_index("ix_uisp_metric_samples_device_observed", table_name="uisp_metric_samples")
    op.drop_table("uisp_metric_samples")
