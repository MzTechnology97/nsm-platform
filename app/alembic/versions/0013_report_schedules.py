"""Scheduled report generation.

Revision ID: 0013_report_schedules
Revises: 0012_generated_reports
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013_report_schedules"
down_revision: Union[str, None] = "0012_generated_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "report_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("output_format", sa.String(10), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("last_period_end", sa.Date(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(20), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_report_id", sa.Uuid(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_report_schedules_enabled", "report_schedules", ["is_enabled"])
    op.add_column("generated_reports", sa.Column("schedule_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_generated_reports_schedule_id",
        "generated_reports",
        "report_schedules",
        ["schedule_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint("fk_generated_reports_schedule_id", "generated_reports", type_="foreignkey")
    op.drop_column("generated_reports", "schedule_id")
    op.drop_index("ix_report_schedules_enabled", table_name="report_schedules")
    op.drop_table("report_schedules")
