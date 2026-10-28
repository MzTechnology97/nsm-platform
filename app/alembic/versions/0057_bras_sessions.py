"""MikroTik BRAS: PPPoE sessions read from the concentrator (BRAS-01).

Revision ID: 0057_bras_sessions
Revises: 0056_ispbilling_handled
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0057_bras_sessions"
down_revision: Union[str, None] = "0056_ispbilling_handled"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "bras_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(120), nullable=False),
        sa.Column("online", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("service", sa.String(20)),
        sa.Column("caller_id", sa.String(64)),
        sa.Column("mac", sa.String(17)),
        sa.Column("address", sa.String(64)),
        sa.Column("session_id", sa.String(40)),
        sa.Column("uptime_seconds", sa.Integer()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("offline_since", sa.DateTime(timezone=True)),
    )
    op.create_index("ux_bras_sessions_username", "bras_sessions", ["username"], unique=True)
    op.create_index("ix_bras_sessions_mac", "bras_sessions", ["mac"])


def downgrade():
    op.drop_index("ix_bras_sessions_mac", table_name="bras_sessions")
    op.drop_index("ux_bras_sessions_username", table_name="bras_sessions")
    op.drop_table("bras_sessions")
