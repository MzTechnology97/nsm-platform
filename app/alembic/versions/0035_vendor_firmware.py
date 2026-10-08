"""Firmware catalogs for manufacturers other than MikroTik (VEND-01).

Revision ID: 0035_vendor_firmware
Revises: 0034_report_recipients
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0035_vendor_firmware"
down_revision: Union[str, None] = "0034_report_recipients"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "vendor_firmware_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("brand", sa.String(40), nullable=False),
        sa.Column("model_pattern", sa.String(120), nullable=False, server_default=""),
        sa.Column("platform", sa.String(40), nullable=False, server_default=""),
        sa.Column("channel", sa.String(30), nullable=False, server_default="stable"),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("released_at", sa.Date(), nullable=True),
        sa.Column("notes_url", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("security", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("brand", "model_pattern", "platform", "version", "source", name="uq_vendor_firmware_release"),
    )
    op.create_index("ix_vendor_firmware_releases_brand", "vendor_firmware_releases", ["brand"])


def downgrade():
    op.drop_index("ix_vendor_firmware_releases_brand", table_name="vendor_firmware_releases")
    op.drop_table("vendor_firmware_releases")
