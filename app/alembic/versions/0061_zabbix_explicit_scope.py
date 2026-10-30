"""Zabbix: the devices of the customers selected so far become explicitly chosen (new devices no longer follow the customer).

Revision ID: 0061_zabbix_explicit_scope
Revises: 0060_ip_assignments
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0061_zabbix_explicit_scope"
down_revision: Union[str, None] = "0060_ip_assignments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    conn = op.get_bind()
    connectors = sa.table("connector_integrations", sa.column("provider", sa.String), sa.column("settings", sa.JSON))
    devices = sa.table("devices", sa.column("id", sa.Uuid), sa.column("customer_id", sa.Uuid), sa.column("inventory_data", sa.JSON))
    settings = conn.execute(sa.select(connectors.c.settings).where(connectors.c.provider == "zabbix")).scalar()
    if not settings or settings.get("scope") != "customers":
        return
    selected = set(settings.get("customer_ids") or [])
    for device_id, customer_id, data in conn.execute(sa.select(devices.c.id, devices.c.customer_id, devices.c.inventory_data)).all():
        data = data or {}
        if str(customer_id) in selected and not data.get("zabbix_exclude"):
            conn.execute(devices.update().where(devices.c.id == device_id).values(inventory_data={**data, "zabbix_include": True}))


def downgrade():
    pass  # the older rule (customer or device) still includes these devices
