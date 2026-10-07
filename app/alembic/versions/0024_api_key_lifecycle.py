"""API key lifecycle: usage evidence and rotation link.

Revision ID: 0024_api_key_lifecycle
Revises: 0023_routeros_releases
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0024_api_key_lifecycle"
down_revision: Union[str, None] = "0023_routeros_releases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("platform_api_keys", sa.Column("last_used_ip", sa.String(64), nullable=True))
    op.add_column("platform_api_keys", sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("platform_api_keys", sa.Column("replaced_by_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_platform_api_keys_replaced_by", "platform_api_keys", "platform_api_keys",
                          ["replaced_by_id"], ["id"], ondelete="SET NULL")


def downgrade():
    op.drop_constraint("fk_platform_api_keys_replaced_by", "platform_api_keys", type_="foreignkey")
    op.drop_column("platform_api_keys", "replaced_by_id")
    op.drop_column("platform_api_keys", "use_count")
    op.drop_column("platform_api_keys", "last_used_ip")
