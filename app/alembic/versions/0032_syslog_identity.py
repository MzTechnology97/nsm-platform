"""Syslog identification hardening (decided 2026-10-08).

Lines that could not be tied to a device are no longer kept: delete those
stored while "accept unknown senders" existed, and the message samples kept
for unknown senders (only counters and reasons remain).

Revision ID: 0032_syslog_identity
Revises: 0031_syslog_auth_events
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0032_syslog_identity"
down_revision: Union[str, None] = "0031_syslog_auth_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.execute("DELETE FROM device_log_entries WHERE device_id IS NULL")
    op.execute("UPDATE syslog_unknown_sources SET sample = NULL")


def downgrade():
    pass
