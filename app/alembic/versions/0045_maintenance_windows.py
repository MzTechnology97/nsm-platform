"""Maintenance windows: planned periods excluded from availability and the customer SLA.

Revision ID: 0045_maintenance_windows
Revises: 0044_customer_sla
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0045_maintenance_windows"
down_revision: Union[str, None] = "0044_customer_sla"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_name", sa.String(150), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_maintenance_windows_period", "maintenance_windows", ["starts_at", "ends_at"])
    op.create_index("ix_maintenance_windows_customer", "maintenance_windows", ["customer_id"])


def downgrade():
    op.drop_index("ix_maintenance_windows_customer", table_name="maintenance_windows")
    op.drop_index("ix_maintenance_windows_period", table_name="maintenance_windows")
    op.drop_table("maintenance_windows")
