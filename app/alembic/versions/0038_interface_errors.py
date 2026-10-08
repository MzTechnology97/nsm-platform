"""Interface errors and drops per traffic sample (MON-01).

Revision ID: 0038_interface_errors
Revises: 0037_user_customer_scope
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0038_interface_errors"
down_revision: Union[str, None] = "0037_user_customer_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = ("rx_errors", "tx_errors", "rx_drops", "tx_drops")


def upgrade():
    for column in COLUMNS:
        op.add_column("device_interface_samples", sa.Column(column, sa.BigInteger(), nullable=True))


def downgrade():
    for column in reversed(COLUMNS):
        op.drop_column("device_interface_samples", column)
