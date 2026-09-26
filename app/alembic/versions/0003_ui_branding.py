"""User UI preferences and platform branding.

Revision ID: 0003_ui_branding
Revises: 0002_core_02
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_ui_branding"
down_revision: Union[str, None] = "0002_core_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("theme", sa.String(20), nullable=False, server_default="light"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "platform_branding",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portal_name", sa.String(160), nullable=False),
        sa.Column("tagline", sa.String(240), nullable=True),
        sa.Column("primary_color", sa.String(7), nullable=False),
        sa.Column("sidebar_color", sa.String(7), nullable=False),
        sa.Column("highlight_color", sa.String(7), nullable=False),
        sa.Column("logo_filename", sa.String(255), nullable=True),
        sa.Column("logo_content_type", sa.String(80), nullable=True),
        sa.Column("logo_data", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    now = datetime.now(timezone.utc)
    branding = sa.table(
        "platform_branding",
        sa.column("id", sa.Integer()),
        sa.column("portal_name", sa.String()),
        sa.column("tagline", sa.String()),
        sa.column("primary_color", sa.String()),
        sa.column("sidebar_color", sa.String()),
        sa.column("highlight_color", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        branding,
        [
            {
                "id": 1,
                "portal_name": "Network Security Platform",
                "tagline": "Network operations & security management",
                "primary_color": "#1f5f8b",
                "sidebar_color": "#111827",
                "highlight_color": "#f59e0b",
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade():
    op.drop_table("platform_branding")
    op.drop_table("user_preferences")
