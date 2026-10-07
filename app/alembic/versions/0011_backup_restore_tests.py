"""Backup restore-test evidence.

Revision ID: 0011_backup_restore_tests
Revises: 0010_firmware_upgrade_plans
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_backup_restore_tests"
down_revision: Union[str, None] = "0010_firmware_upgrade_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "backup_restore_tests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("artifact_filename", sa.String(255), nullable=False),
        sa.Column("artifact_type", sa.String(60), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=True),
        sa.Column("artifact_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("source_routeros_version", sa.String(80), nullable=True),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("target_label", sa.String(200), nullable=False),
        sa.Column("target_routeros_version", sa.String(80), nullable=True),
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("integrity", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("performed_by_user_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["backup_artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["backup_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["performed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backup_restore_tests_device_id", "backup_restore_tests", ["device_id"])
    op.create_index("ix_backup_restore_tests_artifact_id", "backup_restore_tests", ["artifact_id"])
    op.create_index("ix_backup_restore_tests_performed_at", "backup_restore_tests", ["performed_at"])


def downgrade():
    op.drop_index("ix_backup_restore_tests_performed_at", table_name="backup_restore_tests")
    op.drop_index("ix_backup_restore_tests_artifact_id", table_name="backup_restore_tests")
    op.drop_index("ix_backup_restore_tests_device_id", table_name="backup_restore_tests")
    op.drop_table("backup_restore_tests")
