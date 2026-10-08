"""Delegated administration: customers a user may see.

Revision ID: 0037_user_customer_scope
Revises: 0036_cambium_metrics
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0037_user_customer_scope"
down_revision: Union[str, None] = "0036_cambium_metrics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("users", sa.Column("customer_scope", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("users", "customer_scope")
