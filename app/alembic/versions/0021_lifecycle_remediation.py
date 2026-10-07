"""Lifecycle remediation plans and history (LIFE-03).

Revision ID: 0021_lifecycle_remediation
Revises: 0020_lifecycle_catalog
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0021_lifecycle_remediation"
down_revision: Union[str, None] = "0020_lifecycle_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "lifecycle_remediations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("plan_note", sa.Text(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("exception_until", sa.Date(), nullable=True),
        sa.Column("exception_reason", sa.Text(), nullable=True),
        sa.Column("replacement_device_id", sa.Uuid(), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["replacement_device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", name="uq_lifecycle_remediations_device"),
    )
    op.create_table(
        "lifecycle_remediation_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("remediation_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["remediation_id"], ["lifecycle_remediations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lifecycle_remediation_history_rem", "lifecycle_remediation_history", ["remediation_id", "created_at"])


def downgrade():
    op.drop_index("ix_lifecycle_remediation_history_rem", table_name="lifecycle_remediation_history")
    op.drop_table("lifecycle_remediation_history")
    op.drop_table("lifecycle_remediations")
