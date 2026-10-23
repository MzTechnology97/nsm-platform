"""ISPBilling RADIUS accounts matched to NSM devices (ISPB-01).

Revision ID: 0051_ispbilling_radius
Revises: 0050_ispbilling
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0051_ispbilling_radius"
down_revision: Union[str, None] = "0050_ispbilling"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "ispbilling_radius_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("external_id", sa.String(40), nullable=False, unique=True),
        sa.Column("customer_external_id", sa.String(40)),
        sa.Column("address_external_id", sa.String(40)),
        sa.Column("username", sa.String(200), nullable=False),
        sa.Column("static_ip", sa.String(64)),
        sa.Column("profile_name", sa.String(120)),
        sa.Column("status", sa.String(40)),
        sa.Column("cpe_type", sa.String(40)),
        sa.Column("nsm_device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("matched_by", sa.String(10)),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ispbilling_radius_username", "ispbilling_radius_accounts", ["username"])
    op.create_index("ix_ispbilling_radius_device", "ispbilling_radius_accounts", ["nsm_device_id"])


def downgrade():
    op.drop_index("ix_ispbilling_radius_device", table_name="ispbilling_radius_accounts")
    op.drop_index("ix_ispbilling_radius_username", table_name="ispbilling_radius_accounts")
    op.drop_table("ispbilling_radius_accounts")
