"""Firmware upgrade plans.

Revision ID: 0010_firmware_upgrade_plans
Revises: 0009_connector_integrations
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_firmware_upgrade_plans"
down_revision: Union[str, None] = "0009_connector_integrations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "firmware_upgrade_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("target_version", sa.String(150), nullable=False),
        sa.Column("channel", sa.String(40), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("precheck_data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("backup_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["backup_run_id"], ["backup_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_firmware_upgrade_plans_device_id", "firmware_upgrade_plans", ["device_id"])
    op.create_index("ix_firmware_upgrade_plans_status", "firmware_upgrade_plans", ["status"])
    op.create_index("ix_firmware_upgrade_plans_backup_run_id", "firmware_upgrade_plans", ["backup_run_id"])


def downgrade():
    op.drop_index("ix_firmware_upgrade_plans_backup_run_id", table_name="firmware_upgrade_plans")
    op.drop_index("ix_firmware_upgrade_plans_status", table_name="firmware_upgrade_plans")
    op.drop_index("ix_firmware_upgrade_plans_device_id", table_name="firmware_upgrade_plans")
    op.drop_table("firmware_upgrade_plans")
