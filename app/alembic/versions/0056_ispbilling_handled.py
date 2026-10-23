"""ISPBilling automatic import: mark the mirror customers and addresses already handled (ISPB-01).

Revision ID: 0056_ispbilling_handled
Revises: 0053_server_migration
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0056_ispbilling_handled"
down_revision: Union[str, None] = "0053_server_migration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    for table in ("ispbilling_customers", "ispbilling_addresses"):
        op.add_column(table, sa.Column("handled_at", sa.DateTime(timezone=True)))
        # What is already in the mirror counts as handled: the automatic import only looks at what comes next.
        op.execute(f"UPDATE {table} SET handled_at = now()")


def downgrade():
    for table in ("ispbilling_customers", "ispbilling_addresses"):
        op.drop_column(table, "handled_at")
