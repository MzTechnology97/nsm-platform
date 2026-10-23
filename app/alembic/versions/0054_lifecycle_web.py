"""Lifecycle looked up on the web, one row per inventory model.

Revision ID: 0054_lifecycle_web
Revises: 0053_server_migration
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0054_lifecycle_web"
down_revision: Union[str, None] = "0053_server_migration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "lifecycle_web_lookups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("vendor", sa.String(length=60), nullable=False),
        sa.Column("model", sa.String(length=150), nullable=False),
        sa.Column("model_key", sa.String(length=150), nullable=False),
        sa.Column("query", sa.String(length=150)),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("candidates", sa.JSON()),
        sa.Column("error", sa.String(length=255)),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_record_id", sa.Uuid(), sa.ForeignKey("lifecycle_records.id", ondelete="SET NULL")),
        sa.Column("applied_product", sa.String(length=150)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("vendor", "model_key", name="uq_lifecycle_web_lookup_model"),
    )


def downgrade():
    op.drop_table("lifecycle_web_lookups")
