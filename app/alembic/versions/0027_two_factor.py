"""Two-factor authentication (TOTP) and recovery codes.

Revision ID: 0027_two_factor
Revises: 0026_notification_delivery
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0027_two_factor"
down_revision: Union[str, None] = "0026_notification_delivery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("users", sa.Column("totp_secret_encrypted", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("totp_pending_encrypted", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    op.create_table(
        "user_recovery_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_recovery_codes_user_id", "user_recovery_codes", ["user_id"])


def downgrade():
    op.drop_index("ix_user_recovery_codes_user_id", table_name="user_recovery_codes")
    op.drop_table("user_recovery_codes")
    op.drop_column("users", "totp_last_step")
    op.drop_column("users", "totp_enabled_at")
    op.drop_column("users", "totp_pending_encrypted")
    op.drop_column("users", "totp_secret_encrypted")
