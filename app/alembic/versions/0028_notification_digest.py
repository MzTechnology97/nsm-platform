"""Vulnerability newsletter settings and report attachments on deliveries.

Revision ID: 0028_notification_digest
Revises: 0027_two_factor
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0028_notification_digest"
down_revision: Union[str, None] = "0027_two_factor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("users", sa.Column("notify_digest", sa.String(10), nullable=False, server_default="off"))
    op.add_column("users", sa.Column("notify_digest_last_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notification_deliveries", sa.Column("attachment_ref", sa.String(80), nullable=True))


def downgrade():
    op.drop_column("notification_deliveries", "attachment_ref")
    op.drop_column("users", "notify_digest_last_at")
    op.drop_column("users", "notify_digest")
