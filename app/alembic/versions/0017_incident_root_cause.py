"""Incident hypotheses and confirmed root cause (INC-03).

Revision ID: 0017_incident_root_cause
Revises: 0016_incidents
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0017_incident_root_cause"
down_revision: Union[str, None] = "0016_incidents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "incident_hypotheses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("category", sa.String(30), nullable=False, server_default="other"),
        sa.Column("origin", sa.String(20), nullable=False, server_default="operator"),
        sa.Column("confidence", sa.String(20), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incident_hypotheses_incident", "incident_hypotheses", ["incident_id", "created_at"])
    op.add_column("incidents", sa.Column("root_cause_hypothesis_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_incidents_root_cause_hypothesis",
        "incidents",
        "incident_hypotheses",
        ["root_cause_hypothesis_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint("fk_incidents_root_cause_hypothesis", "incidents", type_="foreignkey")
    op.drop_column("incidents", "root_cause_hypothesis_id")
    op.drop_index("ix_incident_hypotheses_incident", table_name="incident_hypotheses")
    op.drop_table("incident_hypotheses")
