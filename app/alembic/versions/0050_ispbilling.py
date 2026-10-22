"""ISPBilling customer registry mirror (ISPB-01): customers, installation addresses, service instances.

Revision ID: 0050_ispbilling
Revises: 0049_acs_cpe_migration
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0050_ispbilling"
down_revision: Union[str, None] = "0049_acs_cpe_migration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "ispbilling_customers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("external_id", sa.String(40), nullable=False, unique=True),
        sa.Column("internal_code", sa.String(80)),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(30)),
        sa.Column("status", sa.String(40)),
        sa.Column("group_name", sa.String(120)),
        sa.Column("address", sa.String(300)),
        sa.Column("nsm_customer_id", sa.Uuid(), sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("linked_at", sa.DateTime(timezone=True)),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ispbilling_customers_code", "ispbilling_customers", ["internal_code"])
    op.create_index("ix_ispbilling_customers_nsm", "ispbilling_customers", ["nsm_customer_id"])
    op.create_table(
        "ispbilling_addresses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("external_id", sa.String(40), nullable=False, unique=True),
        sa.Column("customer_external_id", sa.String(40), nullable=False),
        sa.Column("label", sa.String(200)),
        sa.Column("address", sa.String(300), nullable=False),
        sa.Column("nsm_site_id", sa.Uuid(), sa.ForeignKey("sites.id", ondelete="SET NULL")),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ispbilling_addresses_customer", "ispbilling_addresses", ["customer_external_id"])
    op.create_table(
        "ispbilling_service_instances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("external_id", sa.String(40), nullable=False, unique=True),
        sa.Column("customer_external_id", sa.String(40), nullable=False),
        sa.Column("address_external_id", sa.String(40)),
        sa.Column("name", sa.String(200)),
        sa.Column("status", sa.String(40)),
        sa.Column("remote_updated_at", sa.String(40)),
        sa.Column("detail_read_at", sa.DateTime(timezone=True)),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ispbilling_instances_pending", "ispbilling_service_instances", ["detail_read_at"])


def downgrade():
    op.drop_index("ix_ispbilling_instances_pending", table_name="ispbilling_service_instances")
    op.drop_table("ispbilling_service_instances")
    op.drop_index("ix_ispbilling_addresses_customer", table_name="ispbilling_addresses")
    op.drop_table("ispbilling_addresses")
    op.drop_index("ix_ispbilling_customers_nsm", table_name="ispbilling_customers")
    op.drop_index("ix_ispbilling_customers_code", table_name="ispbilling_customers")
    op.drop_table("ispbilling_customers")
