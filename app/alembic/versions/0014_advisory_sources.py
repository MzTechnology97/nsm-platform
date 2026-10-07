"""Security advisory source provenance and match evidence (SEC-01/02).

Revision ID: 0014_advisory_sources
Revises: 0013_report_schedules
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014_advisory_sources"
down_revision: Union[str, None] = "0013_report_schedules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("security_advisories", sa.Column("source_status", sa.String(40), nullable=True))
    op.add_column("security_advisories", sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "security_advisories",
        sa.Column("match_rules", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.create_index("ix_security_advisories_source", "security_advisories", ["source"])
    op.add_column("device_vulnerabilities", sa.Column("confidence", sa.String(20), nullable=True))
    op.add_column(
        "device_vulnerabilities",
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("device_vulnerabilities", "last_evaluated_at")
    op.drop_column("device_vulnerabilities", "confidence")
    op.drop_index("ix_security_advisories_source", table_name="security_advisories")
    op.drop_column("security_advisories", "match_rules")
    op.drop_column("security_advisories", "fetched_at")
    op.drop_column("security_advisories", "source_status")
