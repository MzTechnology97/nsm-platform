"""Device logbook: operator entries per device (site visits, replacements, customer contacts, notes).

Revision ID: 0043_device_logbook
Revises: 0042_device_decommission
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0043_device_logbook"
down_revision: Union[str, None] = "0042_device_decommission"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "device_journal_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False, server_default="note"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_name", sa.String(150), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_device_journal_entries_device", "device_journal_entries", ["device_id", "occurred_at"])


def downgrade():
    op.drop_index("ix_device_journal_entries_device", table_name="device_journal_entries")
    op.drop_table("device_journal_entries")
