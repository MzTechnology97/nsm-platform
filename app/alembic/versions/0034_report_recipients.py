"""External e-mail recipients for scheduled reports (REP-04).

Revision ID: 0034_report_recipients
Revises: 0033_syslog_chain
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0034_report_recipients"
down_revision: Union[str, None] = "0033_syslog_chain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("report_schedules", sa.Column("recipients", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("report_schedules", "recipients")
