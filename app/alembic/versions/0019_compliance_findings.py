"""Compliance findings handling and history (COMP-03).

Revision ID: 0019_compliance_findings
Revises: 0018_compliance_baseline
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0019_compliance_findings"
down_revision: Union[str, None] = "0018_compliance_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("compliance_results", sa.Column("acknowledged_by_user_id", sa.Uuid(), nullable=True))
    op.add_column("compliance_results", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("compliance_results", sa.Column("exception_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("compliance_results", sa.Column("exception_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_compliance_results_ack_user", "compliance_results", "users", ["acknowledged_by_user_id"], ["id"], ondelete="SET NULL"
    )
    op.create_table(
        "compliance_result_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("result_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["result_id"], ["compliance_results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_compliance_result_history_result", "compliance_result_history", ["result_id", "created_at"])


def downgrade():
    op.drop_index("ix_compliance_result_history_result", table_name="compliance_result_history")
    op.drop_table("compliance_result_history")
    op.drop_constraint("fk_compliance_results_ack_user", "compliance_results", type_="foreignkey")
    op.drop_column("compliance_results", "exception_reason")
    op.drop_column("compliance_results", "exception_until")
    op.drop_column("compliance_results", "acknowledged_at")
    op.drop_column("compliance_results", "acknowledged_by_user_id")
