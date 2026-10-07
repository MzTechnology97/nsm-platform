"""Login throttling and session invalidation.

Revision ID: 0025_login_security
Revises: 0024_api_key_lifecycle
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0025_login_security"
down_revision: Union[str, None] = "0024_api_key_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("users", sa.Column("sessions_valid_after", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "login_failures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_login_failures_user_ip_at", "login_failures", ["username", "ip", "at"])
    op.create_index("ix_login_failures_ip_at", "login_failures", ["ip", "at"])


def downgrade():
    op.drop_index("ix_login_failures_ip_at", table_name="login_failures")
    op.drop_index("ix_login_failures_user_ip_at", table_name="login_failures")
    op.drop_table("login_failures")
    op.drop_column("users", "sessions_valid_after")
