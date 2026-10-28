"""ACS firmware campaigns over many TR-069 CPEs (ACS-08).

Revision ID: 0059_acs_firmware_campaigns
Revises: 0058_acs_pon_optics
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0059_acs_firmware_campaigns"
down_revision: Union[str, None] = "0058_acs_pon_optics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "acs_firmware_campaigns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("product_class", sa.String(120)),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("expected_version", sa.String(120)),
        sa.Column("wave_size", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.String(5), nullable=False),
        sa.Column("window_end", sa.String(5), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("started_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("stop_reason", sa.Text()),
    )
    op.create_table(
        "acs_firmware_campaign_devices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("campaign_id", sa.Uuid(), sa.ForeignKey("acs_firmware_campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("wave", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("from_version", sa.String(120)),
        sa.Column("to_version", sa.String(120)),
        sa.Column("requested_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("reported", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("campaign_id", "device_id", name="uq_acs_campaign_device"),
    )
    op.create_index("ix_acs_campaign_devices_campaign_wave", "acs_firmware_campaign_devices", ["campaign_id", "wave"])


def downgrade():
    op.drop_index("ix_acs_campaign_devices_campaign_wave", table_name="acs_firmware_campaign_devices")
    op.drop_table("acs_firmware_campaign_devices")
    op.drop_table("acs_firmware_campaigns")
