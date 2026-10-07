"""Vendor lifecycle catalog and Device correlation (LIFE-01 / LIFE-02).

Revision ID: 0020_lifecycle_catalog
Revises: 0019_compliance_findings
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0020_lifecycle_catalog"
down_revision: Union[str, None] = "0019_compliance_findings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "lifecycle_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("vendor", sa.String(60), nullable=False),
        sa.Column("model", sa.String(150), nullable=False),
        sa.Column("model_key", sa.String(150), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=True),
        sa.Column("eol_date", sa.Date(), nullable=True),
        sa.Column("eos_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(160), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("evidence_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vendor", "model_key", name="uq_lifecycle_records_vendor_model"),
    )
    op.add_column("devices", sa.Column("lifecycle_match", sa.String(20), nullable=True))
    op.add_column("devices", sa.Column("lifecycle_record_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_devices_lifecycle_record", "devices", "lifecycle_records", ["lifecycle_record_id"], ["id"], ondelete="SET NULL"
    )
    # Lifecycle data entered before the catalog existed is kept as a manual value.
    op.execute(
        "UPDATE devices SET lifecycle_match = 'manual' "
        "WHERE lifecycle_status <> 'unknown' OR eol_date IS NOT NULL OR eos_date IS NOT NULL OR lifecycle_source IS NOT NULL"
    )


def downgrade():
    op.drop_constraint("fk_devices_lifecycle_record", "devices", type_="foreignkey")
    op.drop_column("devices", "lifecycle_record_id")
    op.drop_column("devices", "lifecycle_match")
    op.drop_table("lifecycle_records")
