"""Notifications raised during planned maintenance: the window they fell in.

Revision ID: 0048_notification_maintenance
Revises: 0047_notification_archive
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0048_notification_maintenance"
down_revision: Union[str, None] = "0047_notification_archive"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("notifications", sa.Column("maintenance_window_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_notifications_maintenance_window", "notifications", "maintenance_windows",
                          ["maintenance_window_id"], ["id"], ondelete="SET NULL")


def downgrade():
    op.drop_constraint("fk_notifications_maintenance_window", "notifications", type_="foreignkey")
    op.drop_column("notifications", "maintenance_window_id")
