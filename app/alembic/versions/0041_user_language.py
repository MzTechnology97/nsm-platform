"""Interface language per user (new React interface: it, en, fr, es).

Revision ID: 0041_user_language
Revises: 0040_device_wireless_samples
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0041_user_language"
down_revision: Union[str, None] = "0040_device_wireless_samples"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("user_preferences", sa.Column("language", sa.String(5), nullable=True))


def downgrade():
    op.drop_column("user_preferences", "language")
