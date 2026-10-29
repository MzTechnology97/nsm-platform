"""ACS monitoring: PON optical levels in the CPE samples (ACS-05).

Revision ID: 0058_acs_pon_optics
Revises: 0057_bras_sessions
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0058_acs_pon_optics"
down_revision: Union[str, None] = "0057_bras_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = ("pon_rx_dbm", "pon_tx_dbm", "pon_temperature_c", "pon_voltage_v", "pon_bias_ma")


def upgrade():
    for column in COLUMNS:
        op.add_column("acs_metric_samples", sa.Column(column, sa.Float()))


def downgrade():
    for column in reversed(COLUMNS):
        op.drop_column("acs_metric_samples", column)
