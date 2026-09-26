"""Core 0.6 backup workflow support.

Revision ID: 0004_core06_backups
Revises: 0003_ui_branding
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_core06_backups"
down_revision: Union[str, None] = "0003_ui_branding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "backup_policy_settings",
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scope_site_id", sa.Uuid(), nullable=True),
        sa.Column("schedule_kind", sa.String(30), nullable=False, server_default="daily"),
        sa.Column("schedule_time", sa.String(5), nullable=False, server_default="03:00"),
        sa.Column("schedule_weekday", sa.Integer(), nullable=True),
        sa.Column("schedule_monthday", sa.Integer(), nullable=True),
        sa.Column("options", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["policy_id"], ["backup_policies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scope_site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("policy_id"),
    )
    op.create_index(
        "ix_backup_policy_settings_scope_site_id",
        "backup_policy_settings",
        ["scope_site_id"],
    )
    op.create_table(
        "backup_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_type", sa.String(60), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["backup_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deleted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backup_artifacts_run_id", "backup_artifacts", ["run_id"])
    op.create_index("ix_backup_artifacts_created_at", "backup_artifacts", ["created_at"])
    op.create_index("ix_backup_artifacts_deleted_at", "backup_artifacts", ["deleted_at"])


def downgrade():
    op.drop_index("ix_backup_artifacts_deleted_at", table_name="backup_artifacts")
    op.drop_index("ix_backup_artifacts_created_at", table_name="backup_artifacts")
    op.drop_index("ix_backup_artifacts_run_id", table_name="backup_artifacts")
    op.drop_table("backup_artifacts")
    op.drop_index("ix_backup_policy_settings_scope_site_id", table_name="backup_policy_settings")
    op.drop_table("backup_policy_settings")
