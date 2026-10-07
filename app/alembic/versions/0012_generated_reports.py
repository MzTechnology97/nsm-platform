"""Archived generated reports.

Revision ID: 0012_generated_reports
Revises: 0011_backup_restore_tests
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_generated_reports"
down_revision: Union[str, None] = "0011_backup_restore_tests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "generated_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_type", sa.String(60), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("scope_label", sa.String(200), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("output_format", sa.String(10), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(80), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("generated_by_user_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["generated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generated_reports_generated_at", "generated_reports", ["generated_at"])
    op.create_index("ix_generated_reports_customer_id", "generated_reports", ["customer_id"])


def downgrade():
    op.drop_index("ix_generated_reports_customer_id", table_name="generated_reports")
    op.drop_index("ix_generated_reports_generated_at", table_name="generated_reports")
    op.drop_table("generated_reports")
