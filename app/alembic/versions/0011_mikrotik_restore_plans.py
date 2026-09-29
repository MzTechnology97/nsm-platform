"""MikroTik restore readiness plans.

Revision ID: 0011_mikrotik_restore_plans
Revises: 0010_firmware_upgrade_plans
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_mikrotik_restore_plans"
down_revision: Union[str, None] = "0010_firmware_upgrade_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "mikrotik_restore_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("backup_run_id", sa.Uuid(), nullable=False),
        sa.Column("restore_mode", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("readiness", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["backup_run_id"], ["backup_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mikrotik_restore_plans_device_id", "mikrotik_restore_plans", ["device_id"])
    op.create_index("ix_mikrotik_restore_plans_backup_run_id", "mikrotik_restore_plans", ["backup_run_id"])
    op.create_index("ix_mikrotik_restore_plans_status", "mikrotik_restore_plans", ["status"])


def downgrade():
    op.drop_index("ix_mikrotik_restore_plans_status", table_name="mikrotik_restore_plans")
    op.drop_index("ix_mikrotik_restore_plans_backup_run_id", table_name="mikrotik_restore_plans")
    op.drop_index("ix_mikrotik_restore_plans_device_id", table_name="mikrotik_restore_plans")
    op.drop_table("mikrotik_restore_plans")
