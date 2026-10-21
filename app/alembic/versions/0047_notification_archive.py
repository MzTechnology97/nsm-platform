"""Personal notification archive: archived_at on the per-user read state.

Revision ID: 0047_notification_archive
Revises: 0046_branding_palette
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0047_notification_archive"
down_revision: Union[str, None] = "0046_branding_palette"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("notification_reads", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_notification_reads_user_archived", "notification_reads", ["user_id", "archived_at"])


def downgrade():
    op.drop_index("ix_notification_reads_user_archived", table_name="notification_reads")
    op.drop_column("notification_reads", "archived_at")
