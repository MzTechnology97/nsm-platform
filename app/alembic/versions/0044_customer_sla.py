"""Customer SLA: monthly availability target per customer.

Revision ID: 0044_customer_sla
Revises: 0043_device_logbook
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0044_customer_sla"
down_revision: Union[str, None] = "0043_device_logbook"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("customers", sa.Column("sla_target_pct", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("customers", "sla_target_pct")
