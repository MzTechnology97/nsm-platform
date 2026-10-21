"""Branding colours: the previous factory colours become the new factory palette.

Revision ID: 0046_branding_palette
Revises: 0045_maintenance_windows
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0046_branding_palette"
down_revision: Union[str, None] = "0045_maintenance_windows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# A colour still at the previous factory value moves to the new one; a colour the administrator chose stays.
UPGRADE = (
    "UPDATE platform_branding SET primary_color = '#1769d6' WHERE lower(primary_color) = '#1f5f8b'",
    "UPDATE platform_branding SET sidebar_color = '#10192b' WHERE lower(sidebar_color) = '#111827'",
    "UPDATE platform_branding SET highlight_color = '#f79009' WHERE lower(highlight_color) = '#f59e0b'",
)
DOWNGRADE = (
    "UPDATE platform_branding SET primary_color = '#1f5f8b' WHERE lower(primary_color) = '#1769d6'",
    "UPDATE platform_branding SET sidebar_color = '#111827' WHERE lower(sidebar_color) = '#10192b'",
    "UPDATE platform_branding SET highlight_color = '#f59e0b' WHERE lower(highlight_color) = '#f79009'",
)


def upgrade():
    for statement in UPGRADE:
        op.execute(sa.text(statement))


def downgrade():
    for statement in DOWNGRADE:
        op.execute(sa.text(statement))
