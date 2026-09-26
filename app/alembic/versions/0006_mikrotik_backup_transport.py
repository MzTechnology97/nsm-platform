"""MikroTik encrypted backup upload transport.

Revision ID: 0006_mikrotik_backup_transport
Revises: 0005_mikrotik_agent
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_mikrotik_backup_transport"
down_revision: Union[str, None] = "0005_mikrotik_agent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "mikrotik_backup_job_secrets",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_backup_password", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["job_id"], ["device_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_table(
        "backup_upload_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_type", sa.String(60), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("temp_path", sa.String(1000), nullable=False),
        sa.Column("expected_size", sa.BigInteger(), nullable=True),
        sa.Column("received_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(30), nullable=False, server_default="receiving"),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["device_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "artifact_type", name="uq_backup_upload_job_artifact"),
    )
    op.create_index("ix_backup_upload_sessions_device_id", "backup_upload_sessions", ["device_id"])
    op.create_index("ix_backup_upload_sessions_status", "backup_upload_sessions", ["status"])
    op.create_index("ix_backup_upload_sessions_created_at", "backup_upload_sessions", ["created_at"])


def downgrade():
    op.drop_index("ix_backup_upload_sessions_created_at", table_name="backup_upload_sessions")
    op.drop_index("ix_backup_upload_sessions_status", table_name="backup_upload_sessions")
    op.drop_index("ix_backup_upload_sessions_device_id", table_name="backup_upload_sessions")
    op.drop_table("backup_upload_sessions")
    op.drop_table("mikrotik_backup_job_secrets")
