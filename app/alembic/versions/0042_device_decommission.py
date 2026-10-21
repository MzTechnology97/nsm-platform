"""Reversible device decommission: when, why and by whom a device was taken out of service.

Revision ID: 0042_device_decommission
Revises: 0041_user_language
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0042_device_decommission"
down_revision: Union[str, None] = "0041_user_language"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("devices", sa.Column("decommissioned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("devices", sa.Column("decommission_reason", sa.Text(), nullable=True))
    op.add_column("devices", sa.Column("decommissioned_by_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_devices_decommissioned_by_user", "devices", "users", ["decommissioned_by_user_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_devices_decommissioned_at", "devices", ["decommissioned_at"])


def downgrade():
    op.drop_index("ix_devices_decommissioned_at", table_name="devices")
    op.drop_constraint("fk_devices_decommissioned_by_user", "devices", type_="foreignkey")
    op.drop_column("devices", "decommissioned_by_user_id")
    op.drop_column("devices", "decommission_reason")
    op.drop_column("devices", "decommissioned_at")
