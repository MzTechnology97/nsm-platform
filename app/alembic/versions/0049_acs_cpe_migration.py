"""Bulk CPE migration to the self-hosted ACS: run and per-host result tables.

Revision ID: 0051_acs_cpe_migration
Revises: 0048_notification_maintenance
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0049_acs_cpe_migration"
down_revision: Union[str, None] = "0048_notification_maintenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "acs_migration_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.String(length=150)),
        sa.Column("vendor", sa.String(length=40), nullable=False),
        sa.Column("subnet", sa.String(length=64), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("target_acs_url", sa.String(length=255), nullable=False),
        sa.Column("inform_interval", sa.Integer(), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text()),
        sa.Column("total_hosts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reachable", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("configured", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("already_target", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_acs_migration_runs_created", "acs_migration_runs", ["created_at"])
    op.create_table(
        "acs_migration_hosts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("acs_migration_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("address", sa.String(length=45), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("detail", sa.String(length=255)),
        sa.Column("model", sa.String(length=120)),
        sa.Column("firmware", sa.String(length=150)),
        sa.Column("previous_acs_url", sa.String(length=255)),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_acs_migration_hosts_run", "acs_migration_hosts", ["run_id", "address"])


def downgrade():
    op.drop_index("ix_acs_migration_hosts_run", table_name="acs_migration_hosts")
    op.drop_table("acs_migration_hosts")
    op.drop_index("ix_acs_migration_runs_created", table_name="acs_migration_runs")
    op.drop_table("acs_migration_runs")
