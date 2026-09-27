"""Platform API keys.

Revision ID: 0008_platform_api_keys
Revises: 0007_device_metric_samples
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_platform_api_keys"
down_revision: Union[str, None] = "0007_device_metric_samples"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "platform_api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("key_prefix", sa.String(32), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.create_index("ix_platform_api_keys_customer_id", "platform_api_keys", ["customer_id"])
    op.create_index("ix_platform_api_keys_active", "platform_api_keys", ["is_active"])
    op.create_index("ix_platform_api_keys_prefix", "platform_api_keys", ["key_prefix"], unique=True)


def downgrade():
    op.drop_index("ix_platform_api_keys_prefix", table_name="platform_api_keys")
    op.drop_index("ix_platform_api_keys_active", table_name="platform_api_keys")
    op.drop_index("ix_platform_api_keys_customer_id", table_name="platform_api_keys")
    op.drop_table("platform_api_keys")
