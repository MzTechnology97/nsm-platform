"""Tamper-evident hash chain for warning/error syslog lines.

Revision ID: 0033_syslog_chain
Revises: 0032_syslog_identity
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0033_syslog_chain"
down_revision: Union[str, None] = "0032_syslog_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("device_log_entries", sa.Column("chain_hash", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("device_log_entries", "chain_hash")
