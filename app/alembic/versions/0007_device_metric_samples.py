"""Device telemetry metric samples.

Revision ID: 0007_device_metric_samples
Revises: 0006_mikrotik_backup_transport
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_device_metric_samples"
down_revision: Union[str, None] = "0006_mikrotik_backup_transport"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "device_metric_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("cpu_load", sa.Float(), nullable=True),
        sa.Column("free_memory_bytes", sa.BigInteger(), nullable=True),
        sa.Column("total_memory_bytes", sa.BigInteger(), nullable=True),
        sa.Column("uptime_text", sa.String(100), nullable=True),
        sa.Column("source", sa.String(40), nullable=False, server_default="mikrotik_agent"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_device_metric_samples_device_observed",
        "device_metric_samples",
        ["device_id", "observed_at"],
    )
    op.create_index(
        "ix_device_metric_samples_observed_at",
        "device_metric_samples",
        ["observed_at"],
    )


def downgrade():
    op.drop_index("ix_device_metric_samples_observed_at", table_name="device_metric_samples")
    op.drop_index("ix_device_metric_samples_device_observed", table_name="device_metric_samples")
    op.drop_table("device_metric_samples")
