"""Core 0.2 operational foundation.

Adds observed inventory fields, enrollment, notifications, action center,
security advisories/CVE impacts, backup policies/runs and user metadata.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_core_02"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column("users", sa.Column("display_name", sa.String(160), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("devices", sa.Column("display_name", sa.String(200), nullable=True))
    op.add_column("devices", sa.Column("device_identity", sa.String(200), nullable=True))
    op.add_column("devices", sa.Column("inventory_source", sa.String(60), nullable=True))
    op.add_column("devices", sa.Column("inventory_last_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("devices", sa.Column("architecture", sa.String(100), nullable=True))
    op.add_column("devices", sa.Column("software_id", sa.String(100), nullable=True))
    op.add_column("devices", sa.Column("routerboot_version", sa.String(100), nullable=True))
    op.add_column("devices", sa.Column("inventory_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column("devices", sa.Column("firmware_status", sa.String(30), nullable=False, server_default="unknown"))
    op.add_column("devices", sa.Column("recommended_firmware_version", sa.String(150), nullable=True))
    op.add_column("devices", sa.Column("lifecycle_status", sa.String(30), nullable=False, server_default="unknown"))
    op.add_column("devices", sa.Column("eol_date", sa.Date(), nullable=True))
    op.add_column("devices", sa.Column("eos_date", sa.Date(), nullable=True))
    op.add_column("devices", sa.Column("lifecycle_source", sa.String(255), nullable=True))
    op.add_column("devices", sa.Column("lifecycle_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE devices SET display_name = name WHERE display_name IS NULL")
    op.create_index("ix_devices_serial_number", "devices", ["serial_number"], unique=False)
    op.create_index("ix_devices_device_identity", "devices", ["device_identity"], unique=False)

    op.create_table(
        "security_advisories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cve_id", sa.String(40), nullable=False),
        sa.Column("vendor", sa.String(100), nullable=True),
        sa.Column("product", sa.String(180), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("cvss", sa.Float(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("affected_versions", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("fixed_versions", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("vendor_advisory_url", sa.String(1000), nullable=True),
        sa.Column("nvd_url", sa.String(1000), nullable=True),
        sa.Column("cisa_url", sa.String(1000), nullable=True),
        sa.Column("source", sa.String(80), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cve_id"),
    )
    op.create_index("ix_security_advisories_published_at", "security_advisories", ["published_at"])
    op.create_index("ix_security_advisories_severity", "security_advisories", ["severity"])

    op.create_table(
        "device_vulnerabilities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("advisory_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("installed_version", sa.String(150), nullable=True),
        sa.Column("fixed_version", sa.String(150), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.ForeignKeyConstraint(["advisory_id"], ["security_advisories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("advisory_id", "device_id", name="uq_device_vulnerabilities_advisory_device"),
    )
    op.create_index("ix_device_vulnerabilities_status", "device_vulnerabilities", ["status"])
    op.create_index("ix_device_vulnerabilities_device_id", "device_vulnerabilities", ["device_id"])

    op.create_table(
        "device_enrollments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_device_enrollments_device_id", "device_enrollments", ["device_id"])
    op.create_index("ix_device_enrollments_expires_at", "device_enrollments", ["expires_at"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="info"),
        sa.Column("category", sa.String(40), nullable=False, server_default="system"),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("advisory_id", sa.Uuid(), nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["advisory_id"], ["security_advisories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    op.create_index("ix_notifications_severity", "notifications", ["severity"])
    op.create_index("ix_notifications_category", "notifications", ["category"])

    op.create_table(
        "notification_reads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("notification_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id", "user_id", name="uq_notification_reads_notification_user"),
    )

    op.create_table(
        "action_issues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("advisory_id", sa.Uuid(), nullable=True),
        sa.Column("acknowledged_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["advisory_id"], ["security_advisories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["acknowledged_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_action_issues_status", "action_issues", ["status"])
    op.create_index("ix_action_issues_severity", "action_issues", ["severity"])
    op.create_index("ix_action_issues_device_id", "action_issues", ["device_id"])

    op.create_table(
        "backup_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("scope_type", sa.String(20), nullable=False, server_default="global"),
        sa.Column("vendor", sa.String(60), nullable=True),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("schedule_cron", sa.String(100), nullable=False, server_default="0 3 * * *"),
        sa.Column("binary_backup", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("text_export", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("pre_firmware_backup", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("verify_hash", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("retention_daily", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("retention_weekly", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("retention_monthly", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_backup_policies_scope_type", "backup_policies", ["scope_type"])

    op.create_table(
        "backup_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("backup_type", sa.String(40), nullable=True),
        sa.Column("file_path", sa.String(1000), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["policy_id"], ["backup_policies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backup_runs_device_id", "backup_runs", ["device_id"])
    op.create_index("ix_backup_runs_started_at", "backup_runs", ["started_at"])
    op.create_index("ix_backup_runs_status", "backup_runs", ["status"])


def downgrade():
    op.drop_table("backup_runs")
    op.drop_table("backup_policies")
    op.drop_table("action_issues")
    op.drop_table("notification_reads")
    op.drop_table("notifications")
    op.drop_table("device_enrollments")
    op.drop_table("device_vulnerabilities")
    op.drop_table("security_advisories")
    op.drop_index("ix_devices_device_identity", table_name="devices")
    op.drop_index("ix_devices_serial_number", table_name="devices")
    for col in ["lifecycle_verified_at","lifecycle_source","eos_date","eol_date","lifecycle_status","recommended_firmware_version","firmware_status","inventory_data","routerboot_version","software_id","architecture","inventory_last_verified_at","inventory_source","device_identity","display_name"]:
        op.drop_column("devices", col)
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "email")
    op.drop_column("users", "display_name")
