"""IP attribution: history of the addresses of the BRAS PPPoE sessions (CYBER-01).

Revision ID: 0059_ip_assignments
Revises: 0058_acs_pon_optics
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0059_ip_assignments"
down_revision: Union[str, None] = "0058_acs_pon_optics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "ip_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("address", sa.String(64), nullable=False),
        sa.Column("username", sa.String(120), nullable=False),
        sa.Column("caller_id", sa.String(64)),
        sa.Column("source", sa.String(20), nullable=False, server_default="bras"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_ip_assignments_address", "ip_assignments", ["address", "started_at"])
    op.create_index("ix_ip_assignments_open", "ip_assignments", ["username", "ended_at"])


def downgrade():
    op.drop_table("ip_assignments")
