"""RouterOS release catalog (MTK-04).

Revision ID: 0023_routeros_releases
Revises: 0022_uisp_metric_samples
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0023_routeros_releases"
down_revision: Union[str, None] = "0022_uisp_metric_samples"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "routeros_releases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("security", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("security_lines", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel", "version", name="uq_routeros_releases_channel_version"),
    )


def downgrade():
    op.drop_table("routeros_releases")
