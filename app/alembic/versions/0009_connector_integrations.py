"""Connector integrations.

Revision ID: 0009_connector_integrations
Revises: 0008_platform_api_keys
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_connector_integrations"
down_revision: Union[str, None] = "0008_platform_api_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "connector_integrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_status", sa.String(30), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", name="uq_connector_integrations_provider"),
    )
    op.create_index("ix_connector_integrations_provider", "connector_integrations", ["provider"], unique=True)


def downgrade():
    op.drop_index("ix_connector_integrations_provider", table_name="connector_integrations")
    op.drop_table("connector_integrations")
